"""A4 execution through the framework's public, shared-budget model boundary.

Fake calls also use ``call_model`` and consume the framework's simulated model
and critic counters. They retain A4's ``live=False``/non-billable semantics.
RunContext does not expose model events publicly, so ``framework_calls`` provides
correlation identities that are also placed in each model event's request data.
No private session members or StandaloneExecutor execution paths are used.
"""

from __future__ import annotations

from asyncio import CancelledError, sleep, timeout
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import inspect
import math
import sys
from contextlib import nullcontext
import re
from time import perf_counter
from uuid import uuid4

from poster_framework.errors import BudgetExceededError as FrameworkBudgetExceededError
from poster_framework.runtime import RunContext
from poster_framework.types import AgentRole, ExecutionIdentity, ExecutionMode, JSONValue

from ..contracts import CallRecordV2, TokenUsage
from ..errors import BudgetExceededError, ProviderError, RetryableProviderError
from ..ports import ModelResponse
from ..response_accounting import response_accounting as _response_accounting

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/ -]{0,127}\Z")
_SECRET = re.compile(r"(?i)(?:\bsk-[a-z0-9_-]+|\bbearer\s+\S+|\bbasic\s+\S+|(?:api[_-]?key|password|secret)\s*[=:])")
_SENSITIVE_KEYS = ("apikey", "authorization", "accesstoken", "refreshtoken", "secret", "password", "credential", "privatekey")
_CONTENT_KEYS = {"image", "images", "imagedata", "imagebytes", "base64", "dataurl", "prompt", "messages"}


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) or _SECRET.search(value):
        raise ValueError(f"{label} must be a short, non-secret provider/model identifier")
    return value


def _safe_metadata(value: object, *, key: str | None = None, depth: int = 0) -> JSONValue:
    """Copy event metadata while omitting credentials, content and binary blobs."""
    if depth > 12:
        raise ValueError("request metadata is too deeply nested")
    normalized = "" if key is None else "".join(c for c in key.casefold() if c.isalnum())
    if normalized in {"auth", "token"} or any(part in normalized for part in _SENSITIVE_KEYS):
        return "[REDACTED]"
    if normalized in _CONTENT_KEYS:
        return "[CONTENT OMITTED]"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "[BINARY OMITTED]"
    if isinstance(value, Mapping):
        if len(value) > 256 or any(not isinstance(name, str) for name in value):
            raise ValueError("request metadata must have at most 256 string keys per object")
        if any(len(name) > 128 or _SECRET.search(name) for name in value):
            raise ValueError("request metadata keys must be short and non-secret")
        return {name: _safe_metadata(item, key=name, depth=depth + 1) for name, item in value.items()}
    if isinstance(value, (tuple, list)):
        if len(value) > 256:
            raise ValueError("request metadata arrays must have at most 256 items")
        return [_safe_metadata(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        if _SECRET.search(value):
            return "[REDACTED]"
        if len(value) > 512 or value.startswith(("data:", "iVBORw0KGgo", "/9j/")):
            return "[CONTENT OMITTED]"
        return value
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError("request metadata values must be finite JSON values or omitted binary content")


@dataclass(frozen=True, slots=True)
class FrameworkCallReference:
    """Correlation key, not a claimed framework event ID or billing receipt."""

    identity: ExecutionIdentity
    executor_id: str
    request_index: int
    attempt: int | None
    executed: bool


class FrameworkExecutor:
    """Single-audit Executor whose actual calls are admitted by RunContext.

    Local limits restrict attempts/time further, while the framework remains the
    authority for shared critic/model budgets. Budget denial before invocation
    creates a correlation reference but no A4 billed-attempt record. Retry policy
    belongs to AuditEngine. ``on_update`` is synchronous, as in the local executor.
    """

    def __init__(
        self,
        context: RunContext,
        *,
        provider: str,
        max_attempts: int = 2,
        request_timeout: float = 30.0,
        total_timeout: float = 90.0,
        on_update: Callable[[int, tuple[CallRecordV2, ...]], None] | None = None,
        request_metadata: Mapping[str, object] | None = None,
        parameters: Mapping[str, object] | None = None,
        telemetry=None,
    ) -> None:
        if not isinstance(context, RunContext):
            raise TypeError("context must be a framework RunContext")
        if context.identity.role != AgentRole.quality_critic:
            raise ProviderError("FrameworkExecutor requires the quality_critic role")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 2:
            raise ValueError("max_attempts must be 1 or 2")
        for value in (request_timeout, total_timeout):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 600:
                raise ValueError("timeouts must be finite and in (0, 600] seconds")
        if on_update is not None and (not callable(on_update) or inspect.iscoroutinefunction(on_update)):
            raise TypeError("on_update must be a synchronous callable")
        for value in (request_metadata, parameters):
            if value is not None and not isinstance(value, Mapping):
                raise TypeError("request metadata and parameters must be mappings")
        self.context = context
        self.provider = _identifier(provider, "provider")
        self.max_attempts = max_attempts
        self.request_timeout = request_timeout
        self.total_timeout = total_timeout
        self._metadata = _safe_metadata(request_metadata or {})
        self._parameters = _safe_metadata(parameters or {})
        self._on_update = on_update
        self._started: float | None = None
        self._records: list[CallRecordV2] = []
        self._framework_calls: list[FrameworkCallReference] = []
        self._executor_id = str(uuid4())
        self._busy = False
        if telemetry is not None:
            from ..telemetry import TelemetryRecorder
            if not isinstance(telemetry, TelemetryRecorder):
                raise ValueError('telemetry must be a TelemetryRecorder or null')
        self.telemetry = telemetry

    def _stage(self, name, **kwargs):
        return self.telemetry.stage(name, **kwargs) if self.telemetry else nullcontext()

    @property
    def records(self) -> tuple[CallRecordV2, ...]:
        return tuple(self._records)

    @property
    def framework_calls(self) -> tuple[FrameworkCallReference, ...]:
        return tuple(self._framework_calls)

    @property
    def executor_id(self) -> str:
        return self._executor_id

    def _notify(self, started_attempts: int) -> None:
        if self._on_update is None:
            return
        result = self._on_update(started_attempts, self.records)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError("on_update must return synchronously")

    def record_validation(self, attempt: int, *, error_type: str | None) -> None:
        if type(attempt) is not int or not 1 <= attempt <= len(self._records):
            raise ValueError("unknown attempt")
        old = self._records[attempt - 1]
        if old.validation_status != "pending":
            raise ValueError("attempt has no pending response")
        self._records[attempt - 1] = old.model_copy(update={
            "status": "invalid_response" if error_type else "ok",
            "validation_status": "invalid" if error_type else "valid",
            "error_type": error_type,
        })
        self._notify(len(self._records))

    async def wait_before_retry(self, delay: float) -> None:
        if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not math.isfinite(delay) or delay < 0:
            raise ValueError("retry delay must be finite and nonnegative")
        remaining = self.total_timeout if self._started is None else self.total_timeout - (perf_counter() - self._started)
        if delay >= remaining:
            raise BudgetExceededError("remaining time cannot accommodate the required retry delay")
        if self._busy:
            raise ProviderError("an executor cannot be shared by concurrent audits")
        self._busy = True
        try:
            async with timeout(remaining):
                await sleep(delay)
        except TimeoutError:
            raise BudgetExceededError("audit elapsed-time budget exhausted during retry delay") from None
        finally:
            self._busy = False

    async def execute_once(
        self,
        invoke: Callable[[], Awaitable[ModelResponse]],
        *,
        model: str,
        live: bool,
    ) -> ModelResponse:
        with self._stage('budget_admission'):
            model = _identifier(model, "model")
            if type(live) is not bool or live != (self.context.identity.execution_mode == ExecutionMode.real):
                raise ProviderError("provider mode must match the framework execution identity")
            if not callable(invoke):
                raise TypeError("invoke must be callable")
            if self._busy:
                raise ProviderError("an executor cannot be shared by concurrent audits")
            if len(self._records) >= self.max_attempts:
                raise BudgetExceededError("audit request-attempt budget exhausted")
            now = perf_counter()
            if self._started is None:
                self._started = now
            remaining = self.total_timeout - (now - self._started)
            if remaining <= 0:
                raise BudgetExceededError("audit elapsed-time budget exhausted")
        index = len(self._framework_calls) + 1
        executed = False
        response: ModelResponse | None = None
        usage: TokenUsage | None = None
        accounting: dict[str, object] = {}
        status, error_type = "ok", None
        http_status, request_id = None, None
        started = now

        admission = self._stage('budget_admission')
        admission.__enter__()
        admission_open = True

        async def admitted(_provider: str, _model: str, _request: JSONValue, _parameters: JSONValue) -> ModelResponse:
            nonlocal executed, response, started, error_type, usage, accounting, admission_open
            admission.__exit__(None, None, None)
            admission_open = False
            executed = True
            started = perf_counter()
            try:
                with self._stage('request', attempt=len(self._records) + 1):
                    async with timeout(min(self.request_timeout, remaining)):
                        response = await invoke()
            except TimeoutError:
                error_type = "TimeoutError"
                raise RetryableProviderError("request timed out; billing is unknown") from None
            if not isinstance(response, ModelResponse):
                raise ProviderError("provider must return ModelResponse")
            usage, accounting, invalid = _response_accounting(response)
            if (invalid or type(response.text) is not str or type(response.refusal) is not bool
                    or response.problem is not None and type(response.problem) is not str):
                raise ProviderError("provider returned malformed response metadata") from None
            # Copy validated fields so later provider-owned dictionary mutation cannot
            # invalidate the response or the durable accounting snapshot.
            return ModelResponse(
                text=response.text, usage=usage, problem=response.problem,
                refusal=response.refusal, **accounting,
            )

        self._busy = True
        try:
            # This is intent, not evidence of a billed call. Only admission adds a record.
            self._notify(len(self._records) + 1)
            remaining = self.total_timeout - (perf_counter() - self._started)
            if remaining <= 0:
                raise BudgetExceededError("audit elapsed-time budget exhausted")
            return await self.context.call_model(
                provider=self.provider,
                model=model,
                kind="critic_vision",
                request_data={
                    "a4_execution": {
                        "run_id": self.context.identity.run_id,
                        "invocation_id": self.context.identity.invocation_id,
                        "executor_id": self._executor_id,
                        "request_index": index,
                    },
                    "metadata": _safe_metadata(self._metadata),
                },
                parameters=_safe_metadata(self._parameters),
                invoke=admitted,
            )
        except FrameworkBudgetExceededError as exc:
            status, error_type = "error", type(exc).__name__
            raise BudgetExceededError("framework shared model/critic budget exhausted") from exc
        except CancelledError:
            status, error_type = "error", "CancelledError"
            raise
        except Exception as exc:
            status, error_type = "error", error_type or type(exc).__name__
            if isinstance(exc, ProviderError):
                _, exception_fields, _ = _response_accounting(ModelResponse(
                    text="", http_status=exc.http_status, request_id=exc.request_id,
                ))
                http_status, request_id = exception_fields["http_status"], exception_fields["request_id"]
            raise
        except BaseException as exc:
            status, error_type = "error", type(exc).__name__
            raise
        finally:
            if admission_open:
                admission.__exit__(*sys.exc_info())
            self._busy = False
            self._framework_calls.append(FrameworkCallReference(
                identity=self.context.identity, executor_id=self._executor_id, request_index=index,
                attempt=len(self._records) + 1 if executed else None, executed=executed,
            ))
            if executed:
                self._records.append(CallRecordV2(
                    attempt=len(self._records) + 1, model=model, live=live, status=status,
                    elapsed_ms=round((perf_counter() - started) * 1000, 3),
                    usage=usage, error_type=error_type,
                    transport_status="succeeded" if status == "ok" else "failed",
                    validation_status="pending" if status == "ok" else "not_run",
                    billing_status="not_applicable" if not live else "usage_reported" if usage else "unknown",
                    **({"http_status": http_status, "request_id": request_id} | accounting),
                ))
                self._notify(len(self._records))

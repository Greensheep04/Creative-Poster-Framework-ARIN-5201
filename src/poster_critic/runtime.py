"""Single-audit, bounded execution. Record usage before parsing observations."""

from asyncio import CancelledError, sleep, timeout
from collections.abc import Awaitable, Callable
import math
from time import perf_counter

from .contracts import CallRecordV2
from .errors import BudgetExceededError, ProviderError, RetryableProviderError
from .ports import ModelResponse


class StandaloneExecutor:
    def __init__(self, max_attempts: int = 2, *, allow_live: bool = False,
                 request_timeout: float = 30.0, total_timeout: float = 90.0,
                 on_update: Callable[[int, tuple[CallRecordV2, ...]], None] | None = None) -> None:
        if type(max_attempts) is not int or not 1 <= max_attempts <= 2:
            raise ValueError("max_attempts must be 1 or 2")
        for value in (request_timeout, total_timeout):
            if isinstance(value, bool) or not math.isfinite(value) or not 0 < value <= 600:
                raise ValueError("timeouts must be finite and in (0, 600] seconds")
        if type(allow_live) is not bool:
            raise ValueError("allow_live must be a bool")
        self.max_attempts = max_attempts
        self.allow_live = allow_live
        self.request_timeout = request_timeout
        self.total_timeout = total_timeout
        self._started: float | None = None
        self._records: list[CallRecordV2] = []
        self._busy = False
        self._on_update = on_update

    @property
    def records(self) -> tuple[CallRecordV2, ...]:
        return tuple(self._records)

    def record_validation(self, attempt: int, *, error_type: str | None) -> None:
        if not 1 <= attempt <= len(self._records):
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

    def _notify(self, started_attempts: int) -> None:
        if self._on_update is not None:
            self._on_update(started_attempts, self.records)

    async def wait_before_retry(self, delay: float) -> None:
        if not math.isfinite(delay) or delay < 0:
            raise ValueError('retry delay must be finite and nonnegative')
        remaining = self.total_timeout - (perf_counter() - self._started) if self._started is not None else self.total_timeout
        if delay >= remaining:
            raise BudgetExceededError('remaining time cannot accommodate the required retry delay')
        try:
            async with timeout(remaining):
                await sleep(delay)
        except TimeoutError:
            raise BudgetExceededError('audit elapsed-time budget exhausted during retry delay') from None

    async def execute_once(self, invoke: Callable[[], Awaitable[ModelResponse]], *,
                           model: str, live: bool) -> ModelResponse:
        if live and not self.allow_live:
            raise ProviderError("live execution requires an explicitly enabled executor")
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
        self._notify(len(self._records) + 1)  # Durable intent before the external request.
        self._busy = True
        started = now
        response = None
        status, error_type = "ok", None
        http_status, request_id = None, None
        try:
            async with timeout(min(self.request_timeout, remaining)):
                response = await invoke()
            if not isinstance(response, ModelResponse):
                raise ProviderError("provider must return ModelResponse")
            return response
        except TimeoutError:
            status, error_type = "error", "TimeoutError"
            raise RetryableProviderError("request timed out; billing is unknown") from None
        except CancelledError:
            status, error_type = "error", "CancelledError"
            raise
        except Exception as exc:
            status, error_type = "error", type(exc).__name__
            if isinstance(exc, ProviderError):
                http_status, request_id = exc.http_status, exc.request_id
            raise
        finally:
            self._busy = False
            usage = response.usage if isinstance(response, ModelResponse) else None
            fields = {} if not isinstance(response, ModelResponse) else {
                "request_id": response.request_id, "response_model": response.response_model,
                "finish_reason": response.finish_reason, "http_status": response.http_status,
                "usage_details": response.usage_details,
            }
            self._records.append(CallRecordV2(
                attempt=len(self._records) + 1, model=model, live=live, status=status,
                elapsed_ms=round((perf_counter() - started) * 1000, 3),
                usage=usage, error_type=error_type,
                transport_status="succeeded" if status == "ok" else "failed",
                validation_status="pending" if status == "ok" else "not_run",
                billing_status="not_applicable" if not live else "usage_reported" if usage else "unknown",
                **({"http_status": http_status, "request_id": request_id} | fields),
            ))
            self._notify(len(self._records))

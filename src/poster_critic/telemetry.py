"""Opt-in timing and accounting without raw requests, secrets or QC decisions."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from time import perf_counter
from typing import Annotated, Literal, Self
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from .contracts import AuditInputV1, AuditReportV3, CallRecordV2, Contract, Text
from .errors import InputError
from .evidence import Digest
from .identity import fingerprint, input_fingerprint
from .reporting import write_json

StageName = Literal['input_load', 'input_validate', 'request_prepare', 'budget_admission',
                    'request', 'observation_validate', 'retry_wait', 'report_build',
                    'source_revalidate', 'delivery', 'evidence_review', 'telemetry_write']
Outcome = Literal['completed', 'error', 'cancelled', 'rejected', 'not_run']
Terminal = Literal['completed', 'input_error', 'execution_error', 'delivery_error', 'cancelled']
MeasurementKind = Literal['fake_replay', 'mock_transport', 'live_service', 'unverified']


class BenchmarkCaseV1(Contract):
    spec_sha256: Digest
    arm: Literal['baseline', 'candidate']
    sample_id: Text
    repetition: Annotated[int, Field(ge=1)]
    warmup: bool
    case_id: Digest

    @model_validator(mode='after')
    def valid_case(self) -> Self:
        if fingerprint(self.model_dump(mode='json', exclude={'case_id'})) != self.case_id:
            raise ValueError('benchmark case content differs from its ID')
        return self


class StageTimingV1(Contract):
    stage_id: Text
    name: StageName
    attempt: Annotated[int, Field(ge=1)] | None
    parent_stage_id: Text | None
    elapsed_ms: Annotated[float, Field(ge=0)] | None
    outcome: Outcome

    @model_validator(mode='after')
    def consistent_timer(self) -> Self:
        if (self.outcome == 'not_run') != (self.elapsed_ms is None):
            raise ValueError('only unexecuted stages have null elapsed time')
        return self


class ToolSummaryV1(Contract):
    logical_requests: Annotated[int, Field(ge=0)]
    admitted_attempts: Annotated[int, Field(ge=0)]
    process_starts: Annotated[int, Field(ge=0)]
    denied_requests: Annotated[int, Field(ge=0)]
    failed_requests: Annotated[int, Field(ge=0)]
    cancelled_requests: Annotated[int, Field(ge=0)]


class TelemetryEnvelopeV1(Contract):
    schema_version: Literal[1] = 1
    kind: Literal['a4_telemetry'] = 'a4_telemetry'
    measurement_id: Text
    run_id: str | None
    invocation_id: str | None
    sample_id: str | None
    input_fingerprint: Digest | None
    image_sha256: Digest | None
    # Typed lazily below to keep runtime.py independent of environment probes.
    experiment_identity: dict | None
    execution_mode: Literal['fake', 'live']
    measurement_kind: MeasurementKind
    scope: Literal['audit_only', 'dispatch'] = 'dispatch'
    started_at: Text
    elapsed_total_ms: Annotated[float, Field(ge=0)]
    stages: tuple[StageTimingV1, ...]
    calls: tuple[CallRecordV2, ...]
    audit_decision: Literal['pass', 'fail', 'unknown'] | None
    terminal_outcome: Terminal
    measurement_status: Literal['in_memory', 'recorded', 'write_failed']
    delivery_state: Literal['not_called', 'completed', 'uncertain', 'rejected'] | None
    error_type: str | None
    error_code: str | None
    measurement_error_code: str | None
    benchmark_case: BenchmarkCaseV1 | None
    tool_summary: ToolSummaryV1 | None = None

    @field_validator('schema_version', mode='before')
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError('telemetry version must be an integer')
        return value

    @field_validator('error_type', 'error_code', 'measurement_error_code')
    @classmethod
    def safe_code(cls, value):
        if value is not None and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', value) is None:
            raise ValueError('telemetry accepts only safe short codes')
        return value

    @model_validator(mode='after')
    def consistent_binding(self) -> Self:
        value = datetime.fromisoformat(self.started_at.replace('Z', '+00:00'))
        if value.tzinfo is None or value.utcoffset().total_seconds() != 0:
            raise ValueError('telemetry time label must be UTC')
        if self.execution_mode == 'fake' and self.measurement_kind != 'fake_replay':
            raise ValueError('fake execution must remain fake evidence')
        if self.execution_mode == 'live' and self.measurement_kind == 'fake_replay':
            raise ValueError('live execution cannot be fake replay')
        if len({x.stage_id for x in self.stages}) != len(self.stages):
            raise ValueError('telemetry stages must have unique IDs')
        previous = set()
        for stage in self.stages:
            if stage.parent_stage_id is not None and stage.parent_stage_id not in previous:
                raise ValueError('stage parent must be an earlier declared stage')
            previous.add(stage.stage_id)
        if len({x.attempt for x in self.calls}) != len(self.calls):
            raise ValueError('telemetry call attempts must be unique')
        if any(x.live != (self.execution_mode == 'live') for x in self.calls):
            raise ValueError('telemetry calls must match their execution mode')
        from .ports import ModelResponse
        from .response_accounting import response_accounting, safe_model_identifier
        for call in self.calls:
            from .errors import ProviderError
            try:
                safe_model_identifier(call.model)
            except ProviderError:
                raise ValueError('telemetry model identity is not a safe identifier') from None
            _, _, invalid = response_accounting(ModelResponse(text='', usage=call.usage,
                request_id=call.request_id, response_model=call.response_model,
                finish_reason=call.finish_reason, http_status=call.http_status, usage_details=call.usage_details))
            if invalid:
                raise ValueError('telemetry calls must contain only validated safe accounting fields')
        if self.audit_decision is not None and any(x.validation_status == 'pending' for x in self.calls):
            raise ValueError('a final audit decision cannot have pending observations')
        if self.audit_decision is not None and (self.sample_id is None or self.input_fingerprint is None or self.image_sha256 is None):
            raise ValueError('a decision must be bound to a validated input')
        if self.terminal_outcome == 'completed' and self.audit_decision is None:
            raise ValueError('completed audit telemetry requires a valid report decision')
        if self.delivery_state == 'completed' and self.audit_decision is None:
            raise ValueError('completed dispatch requires a valid report')
        if self.benchmark_case is not None and self.benchmark_case.sample_id != self.sample_id:
            if not (self.sample_id is None and self.input_fingerprint is None and self.image_sha256 is None
                    and self.audit_decision is None and not self.calls and self.terminal_outcome == 'input_error'):
                raise ValueError('benchmark telemetry must match its declared sample')
        if self.scope == 'audit_only' and self.delivery_state not in ('not_called', None):
            raise ValueError('audit-only telemetry cannot claim a delivery')
        if self.measurement_status == 'write_failed' and self.measurement_error_code != 'telemetry_write_failed':
            raise ValueError('telemetry write failure requires its separate error code')
        if self.experiment_identity is not None:
            from .experiment_identity import ExperimentIdentityV1
            ExperimentIdentityV1.model_validate_json(json.dumps(self.experiment_identity))
        return self


class TelemetryWriteError(InputError):
    def __init__(self, envelope: TelemetryEnvelopeV1):
        super().__init__('telemetry_write_failed; prior audit, calls and delivery remain unchanged')
        self.envelope = envelope


def provider_measurement_kind(provider) -> MeasurementKind:
    if not provider.live:
        return 'fake_replay'
    from .providers.chat import ChatProvider
    if isinstance(ChatProvider, type) and isinstance(provider, ChatProvider):
        import httpx
        session = getattr(provider, '_session', None)
        transport = provider._transport if session is None else getattr(session._client, '_transport', provider._transport)
        if transport is None or type(transport) is httpx.AsyncHTTPTransport:
            return 'live_service'
        return 'mock_transport' if type(transport) is httpx.MockTransport else 'unverified'
    # A custom live=True port does not prove network transport or billing.
    return 'unverified'


def reviewer_policy_fingerprint(reviewer) -> str | None:
    """Bind selected gates/configuration, never serialize source callbacks."""
    if reviewer is None:
        return None
    from .evidence_review import EvidenceReviewer, SecondaryReviewConfigV1
    if not isinstance(reviewer, EvidenceReviewer):
        raise InputError('telemetry requires a typed supplementary reviewer policy')
    backend = getattr(reviewer, 'ocr', None)
    config = getattr(backend, 'config', None)
    renderer = getattr(reviewer, 'renderer', None)
    declared = getattr(reviewer, 'frozen_identity_policy', None)
    if declared is not None:
        from .experiment_identity import supplemental_policy_fingerprint
        from .macos_vision import VisionOCR
        from .ocr import TesseractOCR
        if not isinstance(declared, SecondaryReviewConfigV1):
            raise InputError('frozen reviewer identity requires a typed secondary policy')
        try:
            declared = SecondaryReviewConfigV1.model_validate_json(declared.model_dump_json())
        except (ValueError, TypeError, AttributeError):
            raise InputError('invalid frozen reviewer identity policy') from None
        selected = declared.tesseract if declared.backend == 'tesseract' else declared.apple_vision
        backend_type = TesseractOCR if declared.backend == 'tesseract' else VisionOCR
        from .quality_validation import (QualityEvidenceReviewer, QualityTesseractOCR, QualityVisionOCR,
            QUALITY_VISION_OBSERVE,QUALITY_TESSERACT_OBSERVE,QUALITY_REVIEW_CALL,
            NATIVE_VISION_OBSERVE,NATIVE_TESSERACT_OBSERVE,NATIVE_REVIEW_CALL)
        journal_type = QualityTesseractOCR if declared.backend == 'tesseract' else QualityVisionOCR
        expected_backend_methods = {VisionOCR:NATIVE_VISION_OBSERVE,TesseractOCR:NATIVE_TESSERACT_OBSERVE,
            QualityVisionOCR:QUALITY_VISION_OBSERVE,QualityTesseractOCR:QUALITY_TESSERACT_OBSERVE}
        expected_reviewer_methods = {EvidenceReviewer:NATIVE_REVIEW_CALL,QualityEvidenceReviewer:QUALITY_REVIEW_CALL}
        def unchanged_method(instance, name, expected_methods, expected_types):
            selected_type = type(instance)
            method = getattr(instance,name,None)
            function = getattr(method,'__func__',None)
            return (selected_type in expected_types and function is expected_methods.get(selected_type)
                    and function is getattr(selected_type,name,None)
                    and function.__module__ == selected_type.__module__
                    and function.__qualname__ == selected_type.__name__ + '.' + name)
        if (not unchanged_method(backend,'observe',expected_backend_methods,(backend_type,journal_type))
                or not unchanged_method(reviewer,'__call__',expected_reviewer_methods,tuple(expected_reviewer_methods))
                or config != selected
                or reviewer.require_renderer or renderer is not None
                or reviewer.backend_family != declared.backend or reviewer.regions != declared.regions
                or reviewer.max_tool_attempts != declared.max_tool_attempts
                or reviewer.tool_timeout_seconds != declared.tool_timeout_seconds):
            raise InputError('frozen reviewer identity differs from its actual selected policy')
        return supplemental_policy_fingerprint(declared)
    return fingerprint({'kind': 'a4_supplemental_policy', 'schema_version': 1,
        'require_renderer': reviewer.require_renderer, 'renderer_selected': renderer is not None,
        'backend': reviewer.backend_family,
        'config': None if config is None else config.model_dump(mode='json'),
        'config_sha256': getattr(backend, 'config_sha256', None),
        'regions': [x.model_dump(mode='json') for x in reviewer.regions],
        'max_tool_attempts': reviewer.max_tool_attempts, 'tool_timeout_seconds': reviewer.tool_timeout_seconds})


def execution_policy_fingerprint(provider, executor, *, max_retries: int,
                                 retry_delay: float = 0.5, max_retry_delay: float = 5.0) -> str:
    """Bind actual lifecycle and limits so a reuse experiment has distinct arms."""
    session = getattr(provider, '_session', None)
    return fingerprint({'schema_version': 1, 'kind': 'a4_execution_policy',
        'session_policy': 'per_request' if session is None else 'scoped_owned' if session._borrowed is None else 'scoped_borrowed',
        'executor_family': type(executor).__name__,
        'endpoint_family': getattr(provider, 'endpoint_family', None), 'region': getattr(provider, 'region', None),
        'max_attempts': getattr(executor, 'max_attempts', None),
        'request_timeout': getattr(executor, 'request_timeout', None), 'total_timeout': getattr(executor, 'total_timeout', None),
        'max_retries': max_retries, 'retry_delay': retry_delay, 'max_retry_delay': max_retry_delay})


class TelemetryRecorder:
    """One invocation's recorder. Stages overlap; never sum them as total latency."""
    def __init__(self, *, execution_mode: Literal['fake', 'live'], measurement_kind: MeasurementKind,
                 scope: Literal['audit_only', 'dispatch'] = 'audit_only', run_id: str | None = None,
                 invocation_id: str | None = None, benchmark_case: BenchmarkCaseV1 | None = None):
        if execution_mode not in ('fake', 'live') or measurement_kind not in ('fake_replay', 'mock_transport', 'live_service', 'unverified') or scope not in ('audit_only', 'dispatch'):
            raise InputError('invalid explicit telemetry mode or scope')
        if (execution_mode == 'fake') != (measurement_kind == 'fake_replay'):
            raise InputError('telemetry kind must match the explicit execution mode')
        self.execution_mode, self.measurement_kind, self.scope = execution_mode, measurement_kind, scope
        self.measurement_id = str(uuid4())
        self.run_id, self.invocation_id, self.benchmark_case = run_id, invocation_id, benchmark_case
        self.started_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        self._started = perf_counter()
        self._ended: float | None = None
        self._stages: list[StageTimingV1] = []
        self._stack: list[str] = []
        self._input: AuditInputV1 | None = None
        self._identity: dict | None = None
        self._calls: Callable[[], tuple[CallRecordV2, ...]] = lambda: ()
        self._report: AuditReportV3 | None = None
        self._reviewer = None
        self._reviewer_initial_review = self._reviewer_initial_budget = None
        self._terminal: Terminal = 'execution_error'
        self._delivery = 'not_called'
        self._error_type = self._error_code = self._measurement_error = None
        self._measurement_status = 'in_memory'

    def bind_input(self, audit: AuditInputV1) -> None:
        self._mutable()
        from .dispatch import _validated_input
        checked = _validated_input(audit)
        if self._input is not None and input_fingerprint(self._input) != input_fingerprint(checked):
            raise InputError('telemetry recorder belongs to one complete input')
        self._input = checked

    def bind_calls(self, getter: Callable[[], tuple[CallRecordV2, ...]]) -> None:
        self._mutable()
        if not callable(getter):
            raise InputError('telemetry requires a records getter')
        self._calls = getter

    def capture_identity(self, provider, *, language_profile=None, supplemental_policy_sha256=None,
                         execution_policy_sha256=None, environment=None) -> None:
        self._mutable()
        from .experiment_identity import build_experiment_identity
        from .engine import PROMPT_VERSION
        from importlib.resources import files
        identity = build_experiment_identity(prompt_version=PROMPT_VERSION,
            prompt_bytes=files('poster_critic').joinpath('prompts/critic_v4.txt').read_bytes(),
            provider_config=getattr(provider, 'config', None), capability=getattr(provider, 'capability', None),
            language_profile=language_profile, supplemental_policy_sha256=supplemental_policy_sha256,
            execution_policy_sha256=execution_policy_sha256, environment=environment)
        self._identity = identity.model_dump(mode='json')

    def bind_report(self, report: AuditReportV3) -> None:
        self._mutable()
        if self._input is None:
            raise InputError('telemetry report requires a bound input')
        from .dispatch import _bound_report
        self._report = _bound_report(report, self._input, model=report.model, live=self.execution_mode == 'live')

    def identity_unavailable(self) -> None:
        self._mutable()
        self._identity = None
        self._measurement_error = 'identity_capture_failed'

    def bind_reviewer(self, reviewer) -> None:
        self._mutable()
        self._reviewer = reviewer
        self._reviewer_initial_review = getattr(reviewer, 'last_review', None)
        self._reviewer_initial_budget = getattr(reviewer, 'last_tool_budget', None)

    def _mutable(self) -> None:
        if self._ended is not None:
            raise InputError('telemetry recorder belongs to one finalized invocation')

    @contextmanager
    def stage(self, name: StageName, *, attempt: int | None = None) -> Iterator[None]:
        self._mutable()
        stage_id = f's{len(self._stages) + 1}'
        parent = self._stack[-1] if self._stack else None
        # Reserve the entry now so nested parents precede their children.
        index = len(self._stages)
        self._stages.append(StageTimingV1(stage_id=stage_id, name=name, attempt=attempt,
            parent_stage_id=parent, elapsed_ms=0.0, outcome='completed'))
        self._stack.append(stage_id)
        start, outcome = perf_counter(), 'completed'
        try:
            yield
        except BaseException as exc:
            outcome = 'cancelled' if type(exc).__name__ == 'CancelledError' else (
                'rejected' if name == 'budget_admission' else 'error')
            raise
        finally:
            self._stack.pop()
            self._stages[index] = StageTimingV1(stage_id=stage_id, name=name, attempt=attempt,
                parent_stage_id=parent, elapsed_ms=(perf_counter() - start) * 1000, outcome=outcome)

    def not_run(self, name: StageName) -> None:
        self._mutable()
        self._stages.append(StageTimingV1(stage_id=f's{len(self._stages)+1}', name=name, attempt=None,
            parent_stage_id=None, elapsed_ms=None, outcome='not_run'))

    def finish(self, *, terminal_outcome: Terminal, delivery_state=None, error: BaseException | None = None,
               error_code: str | None = None) -> TelemetryEnvelopeV1:
        self._mutable()
        if self._stack:
            raise InputError('telemetry stages must close before finalization')
        if (terminal_outcome not in ('completed', 'input_error', 'execution_error', 'delivery_error', 'cancelled')
                or delivery_state not in (None, 'not_called', 'completed', 'uncertain', 'rejected')):
            raise InputError('telemetry requires an explicit valid terminal/delivery state')
        self._ended, self._terminal, self._delivery = perf_counter(), terminal_outcome, delivery_state
        label = None if error is None else type(error).__name__
        self._error_type = label if label is None or re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', label) else 'OtherError'
        self._error_code = error_code
        return self.snapshot()

    def _tools(self) -> ToolSummaryV1 | None:
        if self._reviewer is None:
            return None
        review = getattr(self._reviewer, 'last_review', None)
        budget = getattr(self._reviewer, 'last_tool_budget', None)
        if review is self._reviewer_initial_review:
            review = None
        if budget is self._reviewer_initial_budget:
            budget = None
        attempts = tuple(x.attempt for x in review.ocr) if review is not None else tuple(getattr(budget, 'attempts', ()))
        def index(x):
            return getattr(x, 'attempt_index', getattr(x, 'attempt', 0))
        return ToolSummaryV1(logical_requests=len(attempts), admitted_attempts=sum(index(x) > 0 for x in attempts),
            process_starts=sum(getattr(x, 'actual_process_starts', getattr(x, 'process_starts', 0)) for x in attempts),
            denied_requests=sum(x.status == 'budget_refused' for x in attempts),
            failed_requests=sum(x.status in ('failed', 'unavailable') for x in attempts),
            cancelled_requests=sum(x.status == 'cancelled' for x in attempts))

    def snapshot(self) -> TelemetryEnvelopeV1:
        if self._ended is None:
            raise InputError('telemetry is available after invocation termination')
        audit, report = self._input, self._report
        return TelemetryEnvelopeV1(measurement_id=self.measurement_id, run_id=self.run_id,
            invocation_id=self.invocation_id, sample_id=None if audit is None else audit.sample_id,
            input_fingerprint=None if audit is None else input_fingerprint(audit), image_sha256=None if audit is None else audit.image.sha256,
            experiment_identity=self._identity, execution_mode=self.execution_mode, measurement_kind=self.measurement_kind,
            scope=self.scope, started_at=self.started_at, elapsed_total_ms=(self._ended - self._started) * 1000,
            stages=tuple(self._stages), calls=self._calls(), audit_decision=None if report is None else report.decision,
            terminal_outcome=self._terminal, measurement_status=self._measurement_status, delivery_state=self._delivery,
            error_type=self._error_type, error_code=self._error_code, measurement_error_code=self._measurement_error,
            benchmark_case=self.benchmark_case, tool_summary=self._tools())

    def write(self, path: Path) -> TelemetryEnvelopeV1:
        # The file cannot contain its own completed publication duration. This
        # stage measures payload preparation before publication, explicitly.
        # Business elapsed_total_ms remains fixed at invocation termination.
        self._measurement_status = 'recorded'
        started = perf_counter()
        self.snapshot().model_dump(mode='json')
        index = len(self._stages)
        self._stages.append(StageTimingV1(stage_id=f's{index+1}', name='telemetry_write',
            attempt=None, parent_stage_id=None, elapsed_ms=(perf_counter()-started)*1000,
            outcome='completed'))
        envelope = self.snapshot()
        try:
            write_json(path, envelope.model_dump(mode='json'))
        except OSError:
            self._measurement_status, self._measurement_error = 'write_failed', 'telemetry_write_failed'
            self._stages[index] = self._stages[index].model_copy(update={'outcome': 'error'})
            raise TelemetryWriteError(self.snapshot()) from None
        return envelope

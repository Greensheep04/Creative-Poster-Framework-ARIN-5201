"""A4-only agent wrapper. The caller owns publication and A1 coordination."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from contextlib import nullcontext
import json

from poster_framework.types import (
    AgentContext, AgentInput, AgentOutput, AgentRole, ContentFormat,
    ExecutionMode, KeywordReviewResult, ReviewRecord,
)

from ..contracts import AuditInputV1, AuditReportV3, CallRecordV2
from ..delivery import DeliveryGuard, DeliveryReceiptV1, delivery_key
from ..dispatch import DispatchResult, _bound_report, audit_and_dispatch
from ..engine import AuditEngine, PROMPT_VERSION
from ..errors import InputError
from ..evidence_review import EvidenceReviewer, SupplementalReviewV1
from ..telemetry import TelemetryRecorder, TelemetryEnvelopeV1, provider_measurement_kind
from ..ports import Provider
from ..structured_diagnostics import structured_diagnostics
from .framework_executor import FrameworkCallReference, FrameworkExecutor
from .framework_input import (
    AuditBinding, FrameworkInputMapping, build_audit_input, validate_binding, validate_binding_snapshot,
)

REPORT_FORMAT = ContentFormat(name="a4.audit_report", version="3")
MappingFactory = Callable[[AgentInput, AgentContext], FrameworkInputMapping]


def _binding_details(binding: AuditBinding) -> dict:
    return {
        "schema_version": 1,
        "identity": binding.identity.model_dump(mode="json"),
        "source_format": binding.source_format.model_dump(mode="json"),
        "candidate": binding.candidate.model_dump(mode="json"),
        "version_dependencies": [item.model_dump(mode="json") for item in binding.version_dependencies],
        "authority": binding.authority,
        "original_request": binding.original_request.model_dump(mode="json"),
        "constraint_ids": list(binding.constraint_ids),
        "content_fingerprint": binding.content_fingerprint,
        "input_fingerprint": binding.input_fingerprint,
    }


def to_framework_output(report: AuditReportV3, binding: AuditBinding) -> AgentOutput:
    """Preserve the full report and its real runtime identity.

    This pure conversion verifies report/input identity, not source freshness.
    The wrapper verifies source freshness immediately before output side effects.
    Action and target stay empty: A1 owns coordination for this dispatch path.
    """
    if not isinstance(binding, AuditBinding) or binding.identity.role != AgentRole.quality_critic:
        raise InputError("framework report requires an A4 audit binding")
    audit = validate_binding_snapshot(binding)
    if not isinstance(report, AuditReportV3):
        raise InputError("framework output requires AuditReportV3")
    report = _bound_report(
        report, audit, model=report.model,
        live=binding.identity.execution_mode == ExecutionMode.real,
    )
    keyword_checks = {item.keyword_id: item for item in report.keyword_checks}
    return AgentOutput(
        content=report.model_dump(mode="json"), content_format=REPORT_FORMAT,
        review=ReviewRecord(
            candidate_id=binding.candidate.artifact_id,
            candidate_revision=binding.candidate.revision,
            reviewer_identity=binding.identity,
            decision=report.decision,
            keyword_results=tuple(
                KeywordReviewResult(keyword_id=item.keyword_id, status=keyword_checks[item.keyword_id].status)
                for item in binding.original_request.keywords
            ),
            details={"binding": _binding_details(binding),
                     "diagnostics": structured_diagnostics(audit, report).model_dump(mode="json")},
        ),
    )


def _with_dispatch(output: AgentOutput, result: DispatchResult,
                   calls: tuple[FrameworkCallReference, ...]) -> AgentOutput:
    # Do not duplicate image bytes or report content in the routing metadata.
    details = dict(output.review.details)
    details["dispatch"] = {
        "schema_version": result.schema_version, "route": result.route,
        "action": result.action, "recipient": result.recipient,
        "handler_status": result.handler_status,
        "input_fingerprint": result.input_fingerprint,
    }
    details["framework_calls"] = [
        {"identity": call.identity.model_dump(mode="json"), "executor_id": call.executor_id,
         "request_index": call.request_index,
         "attempt": call.attempt, "executed": call.executed}
        for call in calls
    ]
    return AgentOutput.model_validate({
        **output.model_dump(mode="python"),
        "review": {**output.review.model_dump(mode="python"), "details": details},
    })


@dataclass(frozen=True, slots=True)
class FrameworkDispatchResult:
    """One handler receives the verified candidate, complete report and binding.

    dispatch.handler_status is pending during the handler. A successful return
    acknowledges only that handler call, not that A1 consumed/repaired the result.
    """

    dispatch: DispatchResult
    binding: AuditBinding
    output: AgentOutput


FrameworkDispatchHandler = Callable[[FrameworkDispatchResult], Awaitable[None]]


class QualityCriticAgent:
    """Implements the existing Agent protocol without editing the shared workflow.

    Both handlers are required. A fresh mapping can be provided per invocation.
    No handler retry, implicit registration or changes to other agent schemas.
    Instances are sequential; create another instance for concurrent invocations.
    """

    def __init__(
        self, provider: Provider, mapping: FrameworkInputMapping | MappingFactory, *,
        on_release: FrameworkDispatchHandler, on_feedback: FrameworkDispatchHandler,
        max_attempts: int = 2, max_retries: int = 1,
        request_timeout: float = 30.0, total_timeout: float = 90.0,
        on_update: Callable[[int, tuple[CallRecordV2, ...]], None] | None = None,
        delivery: DeliveryGuard | None = None,
        evidence_reviewer_factory: Callable[[AuditInputV1, AuditBinding], EvidenceReviewer] | None = None,
        telemetry: bool = False, language_profile=None, expected_framework_revision=None,
    ) -> None:
        if not isinstance(mapping, FrameworkInputMapping) and not callable(mapping):
            raise InputError("A4 requires an explicit input mapping or mapping factory")
        if not callable(on_release) or not callable(on_feedback):
            raise InputError("A4 requires release and A1 feedback handlers")
        self.provider, self.mapping = provider, mapping
        self.on_release, self.on_feedback = on_release, on_feedback
        self.max_attempts, self.max_retries = max_attempts, max_retries
        self.request_timeout, self.total_timeout = request_timeout, total_timeout
        self.on_update = on_update
        self.delivery = delivery
        if evidence_reviewer_factory is not None and not callable(evidence_reviewer_factory):
            raise InputError('evidence_reviewer_factory must be callable or null')
        self.evidence_reviewer_factory = evidence_reviewer_factory
        if type(telemetry) is not bool:
            raise InputError('telemetry enable flag must be a bool')
        if language_profile is not None:
            from ..language_profiles import validate_language_profile
            language_profile = validate_language_profile(language_profile)
        self.telemetry_enabled, self.language_profile = telemetry, language_profile
        self.expected_framework_revision = expected_framework_revision
        self._last_telemetry: TelemetryEnvelopeV1 | None = None
        self._busy = False
        self._records: tuple[CallRecordV2, ...] = ()
        self._framework_calls: tuple[FrameworkCallReference, ...] = ()
        self._last_dispatch: DispatchResult | None = None
        self._last_delivery: DeliveryReceiptV1 | None = None
        self._last_evidence: SupplementalReviewV1 | None = None

    @property
    def records(self) -> tuple[CallRecordV2, ...]:
        """Consumed attempts survive exceptions, including handler failures."""
        return self._records

    @property
    def last_dispatch(self) -> DispatchResult | None:
        """Set only after successful dispatch; never an assertion of A1 repair."""
        return self._last_dispatch

    @property
    def framework_calls(self) -> tuple[FrameworkCallReference, ...]:
        """Correlates admitted/denied requests without claiming event IDs."""
        return self._framework_calls

    @property
    def last_delivery(self) -> DeliveryReceiptV1 | None:
        """Persistent outcome, including uncertainty; not a consumption ACK."""
        return self._last_delivery

    @property
    def last_evidence(self) -> SupplementalReviewV1 | None:
        return self._last_evidence

    @property
    def last_telemetry(self) -> TelemetryEnvelopeV1 | None:
        """Actual finalized in-memory measurement; no automatic file/callback."""
        return self._last_telemetry

    async def run(self, inputs: AgentInput, context: AgentContext) -> AgentOutput:
        if self._busy:
            raise InputError("an A4 agent instance cannot run concurrent audits")
        self._busy = True
        self._records, self._framework_calls, self._last_dispatch = (), (), None
        self._last_delivery = None
        self._last_evidence = None
        executor = None
        engine = None
        recorder = None
        self._last_telemetry = None
        handler_started = False
        handler_completed = False
        try:
            if (type(self.provider.live) is not bool or not isinstance(self.provider.name, str)
                    or not self.provider.name.strip()):
                raise InputError("A4 requires a named provider with an explicit live flag")
            if self.telemetry_enabled:
                recorder = TelemetryRecorder(execution_mode='live' if self.provider.live else 'fake',
                    measurement_kind=provider_measurement_kind(self.provider), scope='dispatch',
                    run_id=context.identity.run_id, invocation_id=context.identity.invocation_id)
            if self.provider.live != (context.identity.execution_mode == ExecutionMode.real):
                raise InputError("A4 provider mode does not match the runtime identity")
            with recorder.stage('input_validate') if recorder else nullcontext():
                mapping = self.mapping if isinstance(self.mapping, FrameworkInputMapping) else self.mapping(inputs, context)
                audit, binding = build_audit_input(inputs, context, mapping)
            executor = FrameworkExecutor(
                context, provider=self.provider.name,
                max_attempts=self.max_attempts, request_timeout=self.request_timeout,
                total_timeout=self.total_timeout, on_update=self.on_update,
                request_metadata={
                    "candidate_id": binding.candidate.artifact_id,
                    "candidate_revision": binding.candidate.revision,
                    "image_sha256": audit.image.sha256,
                    "input_fingerprint": binding.input_fingerprint,
                    "prompt_version": PROMPT_VERSION,
                },
            )
            reviewer = self.evidence_reviewer_factory(audit, binding) if self.evidence_reviewer_factory else None
            if self.evidence_reviewer_factory is not None and not isinstance(reviewer, EvidenceReviewer):
                raise InputError('explicit evidence reviewer factory must return EvidenceReviewer; cannot disable its gate silently')
            engine = AuditEngine(self.provider, executor, max_retries=self.max_retries, evidence_reviewer=reviewer, telemetry=recorder,
                language_profile=self.language_profile, expected_framework_revision=self.expected_framework_revision)

            def with_evidence(output: AgentOutput) -> AgentOutput:
                details = dict(output.review.details)
                if engine.last_evidence is not None:
                    details['supplemental_evidence'] = engine.last_evidence.model_dump(mode='json')
                if self.language_profile is not None:
                    from ..language_profiles import render_diagnostics
                    details['diagnostics_display'] = render_diagnostics(
                        structured_diagnostics(audit, AuditReportV3.model_validate_json(json.dumps(output.content))),
                        self.language_profile).model_dump(mode='json')
                return AgentOutput.model_validate({**output.model_dump(mode='python'),
                    'review': {**output.review.model_dump(mode='python'), 'details': details}})

            class BoundRunner:
                provider = engine.provider

                async def audit(self, working):
                    report = await engine.audit(working)
                    with recorder.stage('source_revalidate') if recorder else nullcontext():
                        validate_binding(binding, context, mapping)
                    return report

            async def deliver(result: DispatchResult) -> None:
                nonlocal handler_started, handler_completed
                with recorder.stage('source_revalidate') if recorder else nullcontext():
                    validate_binding(binding, context, mapping)
                output = with_evidence(_with_dispatch(to_framework_output(result.report, binding), result, executor.framework_calls))
                packet = FrameworkDispatchResult(result, binding, output)
                handler = self.on_release if result.route == "release" else self.on_feedback
                if self.delivery is None:
                    handler_started = True
                    with recorder.stage('delivery') if recorder else nullcontext():
                        await handler(packet)
                    handler_completed = True
                else:
                    # Source/serialization preflight happened before the ledger claim.
                    # The key excludes invocation/output path so re-entry is blocked.
                    identity = f"{binding.identity.run_id}:{binding.candidate.artifact_id}:{binding.candidate.revision}"
                    async def call_handler(_result: DispatchResult) -> None:
                        nonlocal handler_started, handler_completed
                        handler_started = True
                        with recorder.stage('delivery') if recorder else nullcontext():
                            await handler(packet)
                        handler_completed = True
                    key = delivery_key(result, candidate_identity=identity)
                    try:
                        self._last_delivery = await self.delivery.deliver(
                            result, call_handler, candidate_identity=identity,
                        )
                    finally:
                        if self._last_delivery is None:
                            self._last_delivery = self.delivery.lookup(key)

            result = await audit_and_dispatch(
                audit, BoundRunner(), on_release=deliver, on_feedback=deliver,
            )
            output = with_evidence(_with_dispatch(to_framework_output(result.report, binding), result, executor.framework_calls))
            if self._last_delivery is not None:
                details = {**output.review.details, "delivery": self._last_delivery.model_dump(mode="json")}
                output = AgentOutput.model_validate({
                    **output.model_dump(mode="python"),
                    "review": {**output.review.model_dump(mode="python"), "details": details},
                })
            self._last_dispatch = result
            if recorder is not None:
                if result.route == 'test_only':
                    recorder.not_run('delivery')
                self._last_telemetry = recorder.finish(terminal_outcome='completed',
                    delivery_state='not_called' if result.route == 'test_only' else 'completed')
                details = {**output.review.details, 'telemetry': self._last_telemetry.model_dump(mode='json')}
                output = AgentOutput.model_validate({**output.model_dump(mode='python'),
                    'review': {**output.review.model_dump(mode='python'), 'details': details}})
            return output
        except BaseException as exc:
            if recorder is not None and recorder._ended is None:
                state = getattr(self._last_delivery, 'state', None)
                delivery_state = state if state in ('completed', 'uncertain') else 'completed' if handler_completed else 'uncertain' if handler_started else 'rejected' if self._last_delivery else 'not_called'
                terminal = 'cancelled' if type(exc).__name__ == 'CancelledError' or isinstance(exc, KeyboardInterrupt) else 'delivery_error' if handler_started or self._last_delivery else 'input_error' if isinstance(exc, InputError) else 'execution_error'
                self._last_telemetry = recorder.finish(terminal_outcome=terminal, delivery_state=delivery_state, error=exc)
            raise
        finally:
            if executor is not None:
                self._records = executor.records
                self._framework_calls = executor.framework_calls
            if engine is not None:
                self._last_evidence = engine.last_evidence
            self._busy = False

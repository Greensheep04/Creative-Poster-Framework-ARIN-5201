"""One audit algorithm for local callers and future framework adapters."""

from importlib.resources import files
import math
from collections.abc import Awaitable, Callable
from contextlib import nullcontext

from .checks import geometry_findings, parse_observation
from .contracts import AuditInputV1, AuditReportV3
from .errors import InputError, ResponseError, RetryableProviderError
from .policy import make_report
from .ports import Executor, Provider
from .evidence_review import SupplementalReviewV1, apply_supplemental_review

PROMPT_VERSION = "critic-v4"


def validation_reminder(error: ResponseError) -> str:
    # Only trusted rule text is placed in system instructions, never raw model output.
    text = str(error)
    if 'unrequested viewpoint' in text:
        rule = '没有要求视角；删除仰视、俯拍等方向性描述，仅给支持关键词的可见事实。'
    elif 'blind copy observations' in text:
        rule = '盲转录不比较准确性，所有 text_checks.status 必须 unknown；程序负责匹配。'
    elif 'unknown element ID' in text:
        rule = 'element_id 只能选 allowed_element_ids 中的值或 null；不要新编定位 ID。'
    elif 'unreadable observed_text' in text:
        rule = 'observed_text=null 时准确性 status 必须 unknown；不要把看不清改写成不存在。'
    elif 'matching policy' in text:
        rule = '按每项 match_policy 检查文字；符合允许的排版差异不能标准确性 fail。'
    else:
        rule = '完整覆盖声明的 ID 与 Schema；重新看图，仅记录实际可见内容。'
    return '\n校验修正：上一轮观察不符合协议。' + rule


class AuditEngine:
    def __init__(self, provider: Provider, executor: Executor, *, max_retries: int = 1,
                 retry_delay: float = 0.5, max_retry_delay: float = 5.0,
                 evidence_reviewer: Callable[[AuditInputV1, AuditReportV3], Awaitable[SupplementalReviewV1]] | None = None,
                 telemetry=None, language_profile=None, expected_framework_revision=None) -> None:
        if type(max_retries) is not int or not 0 <= max_retries <= 1:
            raise ValueError("max_retries must be 0 or 1")
        if any(isinstance(v, bool) or not math.isfinite(v) or not 0 <= v <= 30
               for v in (retry_delay, max_retry_delay)) or retry_delay > max_retry_delay:
            raise ValueError("retry delays must satisfy 0 <= delay <= maximum <= 30")
        self.provider, self.executor = provider, executor
        self.max_retries, self.retry_delay, self.max_retry_delay = max_retries, retry_delay, max_retry_delay
        if evidence_reviewer is not None and not callable(evidence_reviewer):
            raise ValueError('evidence_reviewer must be callable or null')
        self.evidence_reviewer = evidence_reviewer
        self.last_evidence: SupplementalReviewV1 | None = None
        if telemetry is not None:
            from .telemetry import TelemetryRecorder, provider_measurement_kind
            if (not isinstance(telemetry, TelemetryRecorder)
                    or telemetry.execution_mode != ('live' if provider.live else 'fake')
                    or telemetry.measurement_kind != provider_measurement_kind(provider)):
                raise InputError('telemetry must match this provider and execution mode')
        if language_profile is not None:
            from .language_profiles import validate_language_profile
            language_profile = validate_language_profile(language_profile)
        self.telemetry, self.language_profile = telemetry, language_profile
        self.expected_framework_revision = expected_framework_revision
        if telemetry is not None and hasattr(executor, 'telemetry'):
            if executor.telemetry is not None and executor.telemetry is not telemetry:
                raise InputError('executor belongs to a different telemetry invocation')
            executor.telemetry = telemetry
        self._busy = False

    def _stage(self, name, **kwargs):
        return self.telemetry.stage(name, **kwargs) if self.telemetry else nullcontext()

    async def audit(self, audit: AuditInputV1) -> AuditReportV3:
        if self._busy:
            raise InputError('an audit engine cannot be shared by concurrent audits')
        self._busy = True
        try:
            report = await self._audit(audit)
            if self.telemetry is not None and self.telemetry.scope == 'audit_only':
                self.telemetry.not_run('delivery')
                self.telemetry.finish(terminal_outcome='completed', delivery_state='not_called')
            return report
        except BaseException as exc:
            if self.telemetry is not None and self.telemetry.scope == 'audit_only':
                terminal = 'cancelled' if type(exc).__name__ == 'CancelledError' or isinstance(exc, KeyboardInterrupt) else 'input_error' if isinstance(exc, InputError) else 'execution_error'
                self.telemetry.finish(terminal_outcome=terminal, delivery_state='not_called', error=exc)
            raise
        finally:
            self._busy = False

    async def _audit(self, audit: AuditInputV1) -> AuditReportV3:
        self.last_evidence = None
        start = len(self.executor.records)
        if self.telemetry is not None:
            self.telemetry.bind_calls(lambda: self.executor.records[start:])
            self.telemetry.bind_reviewer(self.evidence_reviewer)
        with self._stage('input_validate'):
            if self.telemetry is not None:
                self.telemetry.bind_input(audit)
                from .telemetry import reviewer_policy_fingerprint, execution_policy_fingerprint
                from .environment import collect_environment
                framework = hasattr(self.executor, 'framework_calls')
                try:
                    environment = collect_environment(scope='framework_adapter' if framework else 'standalone',
                        expected_framework_revision=self.expected_framework_revision if framework else None,
                        require_live=self.provider.live)
                    self.telemetry.capture_identity(self.provider, language_profile=self.language_profile,
                        supplemental_policy_sha256=reviewer_policy_fingerprint(self.evidence_reviewer),
                        execution_policy_sha256=execution_policy_fingerprint(self.provider, self.executor,
                            max_retries=self.max_retries, retry_delay=self.retry_delay, max_retry_delay=self.max_retry_delay),
                        environment=environment)
                except Exception:
                    # Measurement availability cannot veto a valid business audit.
                    # Null identity excludes this result from a bound benchmark.
                    self.telemetry.identity_unavailable()
            preflight = getattr(self.evidence_reviewer, 'preflight', None)
            if callable(preflight):
                preflight(audit)
            program_findings = geometry_findings(audit)
            prompt = files("poster_critic").joinpath("prompts/critic_v4.txt").read_text(encoding="utf-8")
        current_prompt = prompt
        observation, failure = None, None
        for index in range(1 + self.max_retries):
            wait = self.retry_delay if self.provider.live else 0.0
            try:
                prepare = getattr(self.provider, 'prepare_call', None)
                with self._stage('request_prepare'):
                    prepared = prepare(audit, current_prompt) if callable(prepare) else nullcontext(
                        lambda current=current_prompt: self.provider.invoke(audit, current))
                    invoke = prepared.__enter__()
                try:
                    raw = await self.executor.execute_once(invoke, model=self.provider.name, live=self.provider.live)
                finally:
                    import sys
                    prepared.__exit__(*sys.exc_info())
            except RetryableProviderError as exc:
                failure = f"{type(exc).__name__}: {exc}"
                wait = max(wait, exc.retry_after or 0.0)
            else:
                attempt = self.executor.records[-1].attempt
                try:
                    with self._stage('observation_validate', attempt=index + 1):
                        if raw.problem or raw.refusal or raw.finish_reason not in (None, "stop"):
                            raise ResponseError(raw.problem or "provider refused or did not finish the observation")
                        observation = parse_observation(raw.text, audit, blind_copy=getattr(self.provider, 'blind_copy', False))
                except ResponseError as exc:
                    self.executor.record_validation(attempt, error_type=type(exc).__name__)
                    failure = f"{type(exc).__name__}: {exc}"
                    current_prompt = prompt + validation_reminder(exc)
                    if raw.refusal:
                        break
                else:
                    self.executor.record_validation(attempt, error_type=None)
                    failure = None
                    break
            if index == self.max_retries or wait > self.max_retry_delay:
                break
            if wait:
                with self._stage('retry_wait', attempt=index + 2):
                    await self.executor.wait_before_retry(wait)
        with self._stage('report_build'):
            report = make_report(
                audit, observation, program_findings, model=self.provider.name, live=self.provider.live,
                calls=self.executor.records[start:], prompt_version=PROMPT_VERSION, failure=failure,
            )
        if self.evidence_reviewer is not None:
            with self._stage('evidence_review'):
                review = await self.evidence_reviewer(audit, report)
                report = apply_supplemental_review(audit, report, review,
                                                   renderer_source=getattr(self.evidence_reviewer, 'renderer', None))
                self.last_evidence = SupplementalReviewV1.model_validate_json(review.model_dump_json())
        if self.telemetry is not None:
            self.telemetry.bind_report(report)
        return report

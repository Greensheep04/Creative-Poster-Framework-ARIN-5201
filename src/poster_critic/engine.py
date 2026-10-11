"""One audit algorithm for local callers and future framework adapters."""

from importlib.resources import files
import math

from .checks import geometry_findings, parse_observation
from .contracts import AuditInputV1, AuditReportV3
from .errors import ResponseError, RetryableProviderError
from .policy import make_report
from .ports import Executor, Provider

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
                 retry_delay: float = 0.5, max_retry_delay: float = 5.0) -> None:
        if type(max_retries) is not int or not 0 <= max_retries <= 1:
            raise ValueError("max_retries must be 0 or 1")
        if any(isinstance(v, bool) or not math.isfinite(v) or not 0 <= v <= 30
               for v in (retry_delay, max_retry_delay)) or retry_delay > max_retry_delay:
            raise ValueError("retry delays must satisfy 0 <= delay <= maximum <= 30")
        self.provider, self.executor = provider, executor
        self.max_retries, self.retry_delay, self.max_retry_delay = max_retries, retry_delay, max_retry_delay

    async def audit(self, audit: AuditInputV1) -> AuditReportV3:
        program_findings = geometry_findings(audit)
        prompt = files("poster_critic").joinpath("prompts/critic_v4.txt").read_text(encoding="utf-8")
        current_prompt = prompt
        start = len(self.executor.records)
        observation, failure = None, None
        for index in range(1 + self.max_retries):
            wait = self.retry_delay if self.provider.live else 0.0
            try:
                raw = await self.executor.execute_once(
                    lambda current=current_prompt: self.provider.invoke(audit, current),
                    model=self.provider.name, live=self.provider.live,
                )
            except RetryableProviderError as exc:
                failure = f"{type(exc).__name__}: {exc}"
                wait = max(wait, exc.retry_after or 0.0)
            else:
                attempt = self.executor.records[-1].attempt
                try:
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
                await self.executor.wait_before_retry(wait)
        return make_report(
            audit, observation, program_findings, model=self.provider.name, live=self.provider.live,
            calls=self.executor.records[start:], prompt_version=PROMPT_VERSION, failure=failure,
        )

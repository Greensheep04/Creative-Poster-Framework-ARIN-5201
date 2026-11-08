"""Framework-free provider and executor boundaries (executor API v2)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from .contracts import AuditInputV1, CallRecordV2, TokenUsage


@dataclass(frozen=True)
class ModelResponse:
    text: str
    usage: TokenUsage | None = None
    request_id: str | None = None
    response_model: str | None = None
    finish_reason: str | None = None
    http_status: int | None = None
    usage_details: dict[str, int] = field(default_factory=dict)
    problem: str | None = None
    refusal: bool = False


class Provider(Protocol):
    name: str
    live: bool

    async def invoke(self, audit: AuditInputV1, prompt: str) -> ModelResponse: ...


class Executor(Protocol):
    @property
    def records(self) -> tuple[CallRecordV2, ...]: ...

    async def execute_once(
        self, invoke: Callable[[], Awaitable[ModelResponse]], *, model: str, live: bool
    ) -> ModelResponse: ...

    def record_validation(self, attempt: int, *, error_type: str | None) -> None: ...

    async def wait_before_retry(self, delay: float) -> None: ...

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from typing import Annotated, Literal, Protocol, TypeAlias
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, JsonValue, model_validator

INTERFACE_VERSION = "1.0.0"
CANVAS_WIDTH = 720
CANVAS_HEIGHT = 1280


def _validate_json_value(value: object) -> object:
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if math.isfinite(value):
            return value
        raise ValueError("JSON numbers must be finite")
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return value
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            _validate_json_value(item)
        return value
    raise ValueError(f"value of type {type(value).__name__} is not JSON")


JSONValue: TypeAlias = Annotated[JsonValue, BeforeValidator(_validate_json_value)]


class PublicModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AgentRole(StrEnum):
    creative_director = "creative_director"
    layout_planner = "layout_planner"
    visual_producer = "visual_producer"
    quality_critic = "quality_critic"


class ExecutionMode(StrEnum):
    real = "real"
    fake = "fake"


class ReviewDecision(StrEnum):
    pass_ = "pass"
    fail = "fail"
    unknown = "unknown"


class RevisionAction(StrEnum):
    revise_brief = "revise_brief"
    revise_layout = "revise_layout"
    regenerate_asset = "regenerate_asset"
    rerender = "rerender"


class ArtifactPurpose(StrEnum):
    asset = "asset"
    candidate = "candidate"
    evidence = "evidence"


class KeywordReviewStatus(StrEnum):
    pass_ = "pass"
    fail = "fail"
    unknown = "unknown"


class ContentFormat(PublicModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_non_blank(self) -> ContentFormat:
        if not self.name.strip() or not self.version.strip():
            raise ValueError("content format name and version must be non-empty")
        return self


class CanvasSpec(PublicModel):
    width: Literal[720] = CANVAS_WIDTH
    height: Literal[1280] = CANVAS_HEIGHT


class ExecutionIdentity(PublicModel):
    run_id: str = Field(min_length=1)
    role: AgentRole
    invocation_id: str = Field(min_length=1)
    execution_mode: ExecutionMode


class OriginalKeyword(PublicModel):
    keyword_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    position: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_text(self) -> OriginalKeyword:
        if not self.text.strip():
            raise ValueError("keyword text must be non-empty")
        return self


class UserConstraint(PublicModel):
    name: str = Field(min_length=1)
    value: JSONValue

    @model_validator(mode="after")
    def validate_name(self) -> UserConstraint:
        if not self.name.strip():
            raise ValueError("constraint name must be non-empty")
        return self


def _request_id(run_id: str) -> str:
    return f"req-{uuid5(NAMESPACE_URL, f'poster-framework:request:{run_id}')}"


def _keyword_id(run_id: str, position: int, text: str) -> str:
    identity = f"poster-framework:keyword:{run_id}:{position}:{text}"
    return f"kw-{uuid5(NAMESPACE_URL, identity)}"


class OriginalRequest(PublicModel):
    request_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    keywords: tuple[OriginalKeyword, ...]
    constraints: tuple[UserConstraint, ...] = ()
    canvas: CanvasSpec = Field(default_factory=CanvasSpec)

    @classmethod
    def from_keywords(
        cls,
        run_id: str,
        raw_keywords: Sequence[str],
        constraints: Mapping[str, JSONValue] | None = None,
    ) -> OriginalRequest:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")
        if isinstance(raw_keywords, (str, bytes)) or not isinstance(raw_keywords, Sequence):
            raise TypeError("raw_keywords must be a sequence of strings")
        if constraints is not None and not isinstance(constraints, Mapping):
            raise TypeError("constraints must be a mapping")
        keywords = []
        for position, text in enumerate(raw_keywords):
            if not isinstance(text, str):
                raise TypeError("each raw keyword must be a string")
            keywords.append(
                OriginalKeyword(
                    keyword_id=_keyword_id(run_id, position, text),
                    text=text,
                    position=position,
                )
            )
        constraint_rows = tuple(
            UserConstraint(name=name, value=value)
            for name, value in (constraints or {}).items()
        )
        return cls(
            request_id=_request_id(run_id),
            run_id=run_id,
            keywords=tuple(keywords),
            constraints=constraint_rows,
        )

    @model_validator(mode="after")
    def validate_identity_and_keywords(self) -> OriginalRequest:
        if not self.run_id.strip() or self.request_id != _request_id(self.run_id):
            raise ValueError("request identity does not match run_id")
        if not 2 <= len(self.keywords) <= 5:
            raise ValueError("original request requires between two and five keywords")
        for position, keyword in enumerate(self.keywords):
            if keyword.position != position:
                raise ValueError("keyword positions must preserve their original order")
            if keyword.keyword_id != _keyword_id(self.run_id, position, keyword.text):
                raise ValueError("keyword identity does not match the original request")
        return self


class VersionDependency(PublicModel):
    artifact_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)


class FileReference(PublicModel):
    file_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    sha256: str = Field(min_length=1)
    media_type: str = Field(min_length=1)


class ArtifactReference(PublicModel):
    artifact_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    role: AgentRole
    uri: str = Field(min_length=1)
    sha256: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    purpose: ArtifactPurpose = ArtifactPurpose.asset
    source_id: str | None = None


class ContentProvenance(PublicModel):
    run_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    role: AgentRole | None = None
    invocation_id: str | None = None
    revision: str | None = None


class ContentConversionRecord(PublicModel):
    adapter_id: str = Field(min_length=1)
    source_content: JSONValue
    source_format: ContentFormat
    target_content: JSONValue
    target_format: ContentFormat
    source_provenance: ContentProvenance


class KeywordReviewResult(PublicModel):
    keyword_id: str = Field(min_length=1)
    status: KeywordReviewStatus


class ReviewRecord(PublicModel):
    candidate_id: str | None = None
    candidate_revision: str | None = None
    reviewer_identity: ExecutionIdentity | None = None
    decision: ReviewDecision
    action: RevisionAction | None = None
    target: VersionDependency | None = None
    keyword_results: tuple[KeywordReviewResult, ...] = ()
    details: JSONValue | None = None

    @model_validator(mode="after")
    def validate_reviewer_and_keywords(self) -> ReviewRecord:
        if (
            self.reviewer_identity is not None
            and self.reviewer_identity.role != AgentRole.quality_critic
        ):
            raise ValueError("reviewer identity must use the quality_critic role")
        keyword_ids = [result.keyword_id for result in self.keyword_results]
        if len(keyword_ids) != len(set(keyword_ids)):
            raise ValueError("keyword review results must have unique identities")
        if (self.action is None) != (self.target is None):
            raise ValueError("revision action and target must be provided together")
        if self.action is not None and self.decision != ReviewDecision.fail:
            raise ValueError("revision action requires a failed review decision")
        return self


class RunUsage(PublicModel):
    model_calls: int = Field(default=0, ge=0)
    image_operations: int = Field(default=0, ge=0)
    critic_vision_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)


class RunBudget(PublicModel):
    max_revision_rounds: int = Field(default=4, ge=0)
    max_image_operations: int = Field(default=5, ge=0)
    max_critic_vision_calls: int = Field(default=5, ge=0)
    max_model_calls: int = Field(default=100, ge=0)
    max_tool_calls: int = Field(default=100, ge=0)


class FrameworkMetadata(PublicModel):
    interface_version: str = Field(default=INTERFACE_VERSION, min_length=1)
    identity: ExecutionIdentity
    original_request: OriginalRequest
    version_dependencies: tuple[VersionDependency, ...] = ()
    files: tuple[FileReference, ...] = ()
    review_route: tuple[AgentRole, ...] = ()
    revision_action: RevisionAction | None = None
    revision_target: VersionDependency | None = None
    usage: RunUsage = Field(default_factory=RunUsage)
    budget: RunBudget = Field(default_factory=RunBudget)

    @model_validator(mode="after")
    def validate_run_identity(self) -> FrameworkMetadata:
        if self.identity.run_id != self.original_request.run_id:
            raise ValueError("execution and original request run_id must match")
        if len(self.review_route) != len(set(self.review_route)):
            raise ValueError("review route roles must be unique")
        if (self.revision_action is None) != (self.revision_target is None):
            raise ValueError("revision action and target must be provided together")
        return self


class AgentInput(PublicModel):
    content: JSONValue
    content_format: ContentFormat
    metadata: FrameworkMetadata
    provenance: ContentProvenance | None = None


class AgentOutput(PublicModel):
    content: JSONValue
    content_format: ContentFormat
    artifacts: tuple[ArtifactReference, ...] = ()
    review: ReviewRecord | None = None


class AgentContext(Protocol):
    @property
    def identity(self) -> ExecutionIdentity: ...

    @property
    def metadata(self) -> FrameworkMetadata: ...

    @property
    def original_request(self) -> OriginalRequest: ...

    @property
    def tools(self) -> object: ...

    @property
    def files(self) -> object: ...

    @property
    def memory(self) -> object: ...

    async def call_model(
        self,
        *,
        provider: str,
        model: str,
        request_data: JSONValue,
        parameters: JSONValue,
        invoke: Callable[[str, str, JSONValue, JSONValue], object],
        kind: Literal["llm", "image_operation", "critic_vision"] = "llm",
    ) -> object: ...

    def build_model_request(
        self,
        messages: Sequence[JSONValue],
        *,
        max_history_chars: int,
    ) -> JSONValue: ...



class Agent(Protocol):
    async def run(self, inputs: AgentInput, context: AgentContext) -> AgentOutput: ...

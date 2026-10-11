"""Versioned critic-owned contracts, independent of any upstream agent schema."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, field_validator, model_validator

Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
Status = Literal["pass", "fail", "unknown"]
CheckId = Literal[
    "keyword_alignment", "user_constraints", "text_readability",
    "layout_quality", "text_accuracy", "layout_geometry",
]
BASE_CHECKS: tuple[CheckId, ...] = (
    "keyword_alignment", "user_constraints", "text_readability", "layout_quality",
)
ALL_CHECKS: tuple[CheckId, ...] = (*BASE_CHECKS, "text_accuracy", "layout_geometry")
BBox = tuple[float, float, float, float]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


def unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


class Keyword(Contract):
    keyword_id: Text
    text: Text


class UserConstraint(Contract):
    constraint_id: Text
    name: Text
    value: JsonValue

    @model_validator(mode="after")
    def check_required_text(self) -> Self:
        if self.name == "required_text" and (
            not isinstance(self.value, str) or not self.value.strip()
        ):
            raise ValueError("required_text requires a non-empty string value")
        return self


class CopyItem(Contract):
    copy_id: Text
    text: Text
    required: bool = True
    match_policy: Literal["exact", "typographic"] = "exact"


class LayoutElement(Contract):
    element_id: Text
    bbox_xyxy: BBox
    copy_id: Text | None = None


class LayoutContext(Contract):
    elements: tuple[LayoutElement, ...]

    @model_validator(mode="after")
    def check_ids(self) -> Self:
        unique([item.element_id for item in self.elements], "layout element IDs")
        return self


class Asset(Contract):
    asset_key: Text
    element_ids: tuple[Text, ...] = ()
    description: Text | None = None


class AssetContext(Contract):
    assets: tuple[Asset, ...]

    @model_validator(mode="after")
    def check_ids(self) -> Self:
        unique([item.asset_key for item in self.assets], "asset keys")
        return self


class RenderContext(Contract):
    # Diagnostic text is context, not automatically trusted proof of a root cause.
    messages: tuple[Text, ...] = ()


class AuditSpec(Contract):
    schema_version: Literal[1] = 1
    sample_id: Text
    keywords: Annotated[tuple[Keyword, ...], Field(min_length=2, max_length=5)]
    user_constraints: tuple[UserConstraint, ...]
    required_checks: tuple[CheckId, ...] = BASE_CHECKS
    approved_copy: tuple[CopyItem, ...] | None = None
    design_context: Text | None = None
    layout_context: LayoutContext | None = None
    asset_context: AssetContext | None = None
    render_context: RenderContext | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def check_spec(self) -> Self:
        unique([item.keyword_id for item in self.keywords], "keyword IDs")
        unique([item.constraint_id for item in self.user_constraints], "constraint IDs")
        unique([item.copy_id for item in self.approved_copy or ()], "copy IDs")
        unique(list(self.required_checks), "required checks")
        if not set(BASE_CHECKS).issubset(self.required_checks):
            raise ValueError("the four baseline checks cannot be disabled")
        needs_text = any(item.required for item in self.approved_copy or ()) or any(
            item.name == "required_text" for item in self.user_constraints
        )
        if needs_text and "text_accuracy" not in self.required_checks:
            object.__setattr__(self, "required_checks", (*self.required_checks, "text_accuracy"))
        return self


class ImagePayload(Contract):
    data: bytes = Field(exclude=True, repr=False, min_length=1)
    media_type: Literal["image/png", "image/jpeg"]
    width: Literal[720] = 720
    height: Literal[1280] = 1280
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def check_hash(self) -> Self:
        if hashlib.sha256(self.data).hexdigest() != self.sha256:
            raise ValueError("image digest does not match the supplied bytes")
        return self


class AuditInputV1(AuditSpec):
    image: ImagePayload


class ImageLocation(Contract):
    path: Text


class LocalManifest(AuditSpec):
    image: ImageLocation


class KeywordCheck(Contract):
    keyword_id: Text
    status: Status
    evidence: Text


class ConstraintCheck(Contract):
    constraint_id: Text
    status: Status
    evidence: Text


class TextCheck(Contract):
    copy_id: Text
    observed_text: str | None
    status: Status
    readability: Status


class VisualCheck(Contract):
    check_id: Literal["text_readability", "layout_quality"]
    status: Status
    evidence: Text


class Issue(Contract):
    code: Text
    severity: Literal["blocking", "major", "minor"]
    element_id: Text | None = None
    bbox_xyxy: BBox | None = None
    evidence: Text
    suggestion: Text


class ModelIssue(Issue):
    @model_validator(mode="after")
    def valid_observed_box(self) -> Self:
        if self.bbox_xyxy is not None:
            x0, y0, x1, y1 = self.bbox_xyxy
            if not (0 <= x0 < x1 <= 720 and 0 <= y0 < y1 <= 1280):
                raise ValueError("model observation box must be inside the 720x1280 canvas")
        return self


class Finding(Issue):
    source: Literal["program", "model"]


class ModelObservation(Contract):
    keyword_checks: tuple[KeywordCheck, ...]
    constraint_checks: tuple[ConstraintCheck, ...]
    text_checks: tuple[TextCheck, ...]
    visual_checks: tuple[VisualCheck, ...]
    issues: tuple[ModelIssue, ...]
    uncertainties: tuple[Text, ...]


class Coverage(Contract):
    check_id: CheckId
    required: bool
    state: Literal["assessed", "not_assessed", "unknown"]
    status: Status | None
    reason: Text

    @model_validator(mode="after")
    def consistent_state(self) -> Self:
        if self.state == "not_assessed":
            if self.required or self.status is not None:
                raise ValueError("only optional unchecked items can be not_assessed")
        elif self.state == "unknown":
            if self.status != "unknown":
                raise ValueError("unknown coverage requires unknown status")
        elif self.status not in ("pass", "fail"):
            raise ValueError("assessed coverage requires pass or fail")
        return self


class RepairHint(Contract):
    stage: Literal["brief", "layout", "asset", "render"]
    element_id: Text | None = None
    asset_key: Text | None = None
    reason: Text
    preserve: Text


class TokenUsage(Contract):
    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]


class CallRecord(Contract):
    attempt: Annotated[int, Field(ge=1)]
    model: Text
    live: bool
    status: Literal["ok", "invalid_response", "error"]
    elapsed_ms: Annotated[float, Field(ge=0)]
    usage: TokenUsage | None = None
    error_type: Text | None = None


class AuditReportV1(Contract):
    schema_version: Literal[1] = 1
    sample_id: Text
    image_sha256: Text
    decision: Status
    keyword_checks: Annotated[tuple[KeywordCheck, ...], Field(min_length=2, max_length=5)]
    constraint_checks: tuple[ConstraintCheck, ...]
    text_checks: tuple[TextCheck, ...]
    coverage: tuple[Coverage, ...]
    issues: tuple[Finding, ...]
    uncertainties: tuple[Text, ...]
    repair_hint: RepairHint | None
    model: Text
    prompt_version: Text
    execution_mode: Literal["fake", "live"]
    calls: tuple[CallRecord, ...]
    vision_calls: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def consistent_decision(self) -> Self:
        unique([item.keyword_id for item in self.keyword_checks], "report keyword IDs")
        unique([item.constraint_id for item in self.constraint_checks], "report constraint IDs")
        unique([item.copy_id for item in self.text_checks], "report copy IDs")
        unique([item.check_id for item in self.coverage], "coverage IDs")
        if {item.check_id for item in self.coverage} != set(ALL_CHECKS):
            raise ValueError("report coverage must explicitly include all six checks")
        if not set(BASE_CHECKS).issubset({item.check_id for item in self.coverage if item.required}):
            raise ValueError("report cannot disable baseline checks")
        if self.repair_hint is not None and self.decision != "fail":
            raise ValueError("repair hints require a failed audit")
        if self.decision == "pass" and (
            any(item.status != "pass" for item in self.keyword_checks)
            or any(item.required and item.status != "pass" for item in self.coverage)
            or any(item.severity != "minor" for item in self.issues)
            or self.uncertainties
        ):
            raise ValueError("pass is inconsistent with failures or missing evidence")
        if self.vision_calls != sum(item.live for item in self.calls):
            raise ValueError("vision call count must match recorded live attempts")
        incomplete = self.uncertainties or any(
            item.required and item.status == "unknown" for item in self.coverage
        ) or any(item.status == "unknown" for item in self.keyword_checks)
        failed = any(item.required and item.status == "fail" for item in self.coverage) or any(
            item.severity != "minor" for item in self.issues
        ) or any(item.status == "fail" for item in self.keyword_checks)
        if self.schema_version >= 3:
            expected = "fail" if failed else "unknown" if incomplete else "pass"
            if self.repair_hint is not None and incomplete:
                raise ValueError("automatic repair is unsafe with incomplete evidence")
        else:
            expected = "unknown" if incomplete else "fail" if failed else "pass"
        if self.decision != expected:
            raise ValueError(f"decision must be {expected} for the recorded evidence")
        return self


class CallRecordV2(CallRecord):
    """Transport accounting survives observation validation failures."""

    transport_status: Literal["succeeded", "failed"]
    validation_status: Literal["pending", "valid", "invalid", "not_run"]
    billing_status: Literal["not_applicable", "usage_reported", "unknown"]
    request_id: str | None = None
    response_model: str | None = None
    finish_reason: str | None = None
    http_status: int | None = None
    usage_details: dict[str, int] = Field(default_factory=dict)


class AuditReportV2(AuditReportV1):
    schema_version: Literal[2] = 2
    calls: tuple[CallRecordV2, ...]

    @model_validator(mode="after")
    def no_pending_calls(self) -> Self:
        if any(c.validation_status == "pending" for c in self.calls):
            raise ValueError("an audit report cannot contain unvalidated responses")
        return self


class AuditReportV3(AuditReportV2):
    """Known required failures take precedence; incomplete audits cannot auto-repair."""

    schema_version: Literal[3] = 3


def parse_report(raw: str) -> AuditReportV1 | AuditReportV2 | AuditReportV3:
    """Read each saved report under its original version's decision semantics."""
    import json
    value = json.loads(raw)
    version = value.get("schema_version") if isinstance(value, dict) else None
    if type(version) is not int or version not in (1, 2, 3):
        raise ValueError("unsupported audit report version")
    return {1: AuditReportV1, 2: AuditReportV2, 3: AuditReportV3}[version].model_validate_json(raw)

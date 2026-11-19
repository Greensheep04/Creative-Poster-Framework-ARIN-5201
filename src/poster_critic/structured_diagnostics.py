"""A4-owned, versioned evidence gaps for consumers of a bound V3 report.

This projection reads typed fields, never the wording of human diagnostics.
It preserves the original report decision and evidence. Successful recorded
validation is audit provenance, not proof that a remote service was contacted
or that an independent human confirmed image quality.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import StringConstraints, field_validator, model_validator

from .contracts import (
    ALL_CHECKS, BASE_CHECKS, AuditInputV1, AuditReportV3, CallRecordV2, CheckId, Contract, Coverage,
    Finding, Status, Text, unique,
)
from .dispatch import _bound_report, _validate_image_bytes, _validated_input
from .errors import InputError
from .identity import input_fingerprint

Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
DiagnosticCode = Literal[
    "review_incomplete", "keyword_unknown", "keyword_failed",
    "constraint_unknown", "constraint_failed", "required_text_mapping_missing",
    "copy_unknown", "copy_failed", "copy_readability_unknown",
    "copy_readability_failed", "approved_copy_missing", "layout_context_missing",
    "check_unknown", "check_failed", "check_not_assessed", "report_uncertainty_unlocated",
]
SubjectKind = Literal["keyword", "constraint", "copy", "check", "execution", "report_uncertainty"]


class DiagnosticItem(Contract):
    code: DiagnosticCode
    status: Literal["fail", "unknown", "not_assessed"]
    subject_kind: SubjectKind
    subject_id: Text | None
    check_ids: tuple[CheckId, ...]
    required: bool
    blocks_release: bool
    doubt: Text
    missing_evidence: Text
    suggestion: Text
    observed_evidence: str | None = None

    @model_validator(mode="after")
    def consistent_subject(self) -> Self:
        unique(list(self.check_ids), "diagnostic check IDs")
        if self.code.startswith("keyword_"):
            expected_kind, expected_checks = "keyword", ("keyword_alignment",)
        elif self.code.startswith("constraint_"):
            expected_kind, expected_checks = "constraint", ("user_constraints",)
        elif self.code == "required_text_mapping_missing":
            expected_kind, expected_checks = "constraint", ("user_constraints", "text_accuracy")
        elif self.code.startswith("copy_"):
            expected_kind = "copy"
            expected_checks = ("text_readability",) if self.code.startswith("copy_readability_") else ("text_accuracy",)
        elif self.code == "review_incomplete":
            expected_kind, expected_checks = "execution", self.check_ids
        elif self.code == "report_uncertainty_unlocated":
            expected_kind, expected_checks = "report_uncertainty", ()
        elif self.code == "approved_copy_missing":
            expected_kind, expected_checks = "check", ("text_accuracy",)
        elif self.code == "layout_context_missing":
            expected_kind, expected_checks = "check", ("layout_geometry",)
        else:
            expected_kind, expected_checks = "check", (self.subject_id,)
        if self.subject_kind != expected_kind or self.check_ids != expected_checks:
            raise ValueError("diagnostic code must match its typed subject and checks")
        if self.code.endswith("_failed") and self.status != "fail":
            raise ValueError("failed diagnostic codes require fail status")
        if self.code == "check_not_assessed" and self.status != "not_assessed":
            raise ValueError("unassessed diagnostic codes require not_assessed status")
        missing_context = self.code in ("approved_copy_missing", "layout_context_missing")
        if missing_context:
            expected_status = "unknown" if self.required else "not_assessed"
            if self.status != expected_status:
                raise ValueError("missing context status must match its requirement")
        elif not self.code.endswith("_failed") and self.code != "check_not_assessed" and self.status != "unknown":
            raise ValueError("uncertainty diagnostic codes require unknown status")
        unlocated = self.subject_kind in ("execution", "report_uncertainty")
        if unlocated != (self.subject_id is None):
            raise ValueError("only execution and report uncertainties have an unlocated subject")
        if self.subject_kind == "check" and self.subject_id not in self.check_ids:
            raise ValueError("a check subject must appear in its check IDs")
        if unlocated and not self.required:
            raise ValueError("unlocated audit uncertainties are release-blocking requirements")
        if self.subject_kind == "execution" and not set(BASE_CHECKS).issubset(self.check_ids):
            raise ValueError("an incomplete review must include baseline checks")
        if self.status == "not_assessed" and (self.required or self.blocks_release):
            raise ValueError("an unassessed optional check cannot block release")
        if self.blocks_release != (self.required and self.status != "not_assessed"):
            raise ValueError("release blocking must match the diagnostic requirement")
        return self


class DiagnosticsEnvelopeV1(Contract):
    """Bound projection; human reasons remain separate from machine fields."""

    schema_version: Literal[1] = 1
    sample_id: Text
    image_sha256: Digest
    input_fingerprint: Digest
    decision: Status
    items: tuple[DiagnosticItem, ...]
    human_uncertainties: tuple[Text, ...]
    coverage: tuple[Coverage, ...]
    findings: tuple[Finding, ...]
    calls: tuple[CallRecordV2, ...]

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def consistent_projection(self) -> Self:
        unique([item.check_id for item in self.coverage], "coverage IDs")
        if {item.check_id for item in self.coverage} != set(ALL_CHECKS):
            raise ValueError("diagnostics coverage must include all six checks")
        if not set(BASE_CHECKS).issubset({item.check_id for item in self.coverage if item.required}):
            raise ValueError("diagnostics cannot disable baseline checks")
        _finalized_calls(self.calls, self.decision)
        if self.decision == "pass" and (
            self.human_uncertainties or any(item.blocks_release for item in self.items)
            or any(item.required and item.status != "pass" for item in self.coverage)
            or any(item.severity != "minor" for item in self.findings)
        ):
            raise ValueError("a passed envelope cannot contain a release-blocking uncertainty")
        return self


def _finalized_calls(calls: tuple[CallRecordV2, ...], decision: Status) -> bool:
    """Require final, internally consistent accounting before interpreting it."""
    if len({call.attempt for call in calls}) != len(calls):
        raise ValueError("diagnostics require unique recorded call attempts")
    expected = {
        "ok": ("succeeded", "valid"),
        "invalid_response": ("succeeded", "invalid"),
        "error": ("failed", "not_run"),
    }
    for call in calls:
        if (call.transport_status, call.validation_status) != expected[call.status]:
            raise ValueError("diagnostics require consistent finalized call records")
    effective = any(call.status == "ok" for call in calls)
    if decision == "pass" and not effective:
        raise ValueError("passed diagnostics require a recorded valid observation")
    return effective


def structured_diagnostics(audit: AuditInputV1, report: AuditReportV3) -> DiagnosticsEnvelopeV1:
    """Project a validated current input/report pair without modifying either.

    All subject IDs come from typed input/report fields. The complete input
    fingerprint binds supplied content, but does not establish which revision
    an external candidate service currently considers latest. Unknown optional
    checks stay optional and never change a passed report into an unknown one.
    """
    working = _validated_input(audit)
    _validate_image_bytes(working)
    if not isinstance(report, AuditReportV3):
        raise InputError("diagnostics require the current AuditReportV3 contract")
    bound = _bound_report(
        report, working, model=report.model, live=report.execution_mode == "live",
    )
    try:
        effective = _finalized_calls(bound.calls, bound.decision)
    except ValueError as exc:
        raise InputError(str(exc)) from exc
    by_check = {item.check_id: item for item in bound.coverage}
    items: list[DiagnosticItem] = []

    def add(
        code: DiagnosticCode, status: Literal["fail", "unknown", "not_assessed"],
        kind: SubjectKind, subject_id: str | None, check_ids: tuple[CheckId, ...],
        doubt: str, missing: str, suggestion: str, *, evidence: str | None = None,
        required: bool | None = None,
    ) -> None:
        is_required = any(by_check[c].required for c in check_ids) if required is None else required
        items.append(DiagnosticItem(
            code=code, status=status, subject_kind=kind, subject_id=subject_id,
            check_ids=check_ids, required=is_required,
            blocks_release=is_required and status != "not_assessed",
            doubt=doubt, missing_evidence=missing, suggestion=suggestion,
            observed_evidence=evidence,
        ))

    if not effective:
        error_types = tuple(dict.fromkeys(call.error_type for call in bound.calls if call.error_type))
        add(
            "review_incomplete", "unknown", "execution", None, working.required_checks,
            "No successful, protocol-valid observation is recorded for this candidate.",
            "A valid recorded observation covering the current audit requirements.",
            "A1 should inspect the actual call records, verify the candidate and requirements, and request a new audit.",
            evidence=", ".join(error_types) if error_types else None, required=True,
        )

    for check in bound.keyword_checks:
        if check.status != "pass":
            add(
                "keyword_unknown" if check.status == "unknown" else "keyword_failed",
                check.status, "keyword", check.keyword_id, ("keyword_alignment",),
                "The keyword's visual expression is unconfirmed." if check.status == "unknown"
                else "The recorded keyword check failed.",
                "Recognizable candidate evidence supporting the original keyword.",
                "A1 should compare this keyword with the current candidate and obtain evidence before choosing a production change.",
                evidence=check.evidence,
            )

    missing_literals = {
        item.constraint_id for item in working.user_constraints
        if item.name == "required_text" and not any(
            copy.required and copy.text == item.value for copy in working.approved_copy or ()
        )
    }
    for check in bound.constraint_checks:
        if check.constraint_id in missing_literals:
            add(
                "required_text_mapping_missing", "unknown", "constraint", check.constraint_id,
                ("user_constraints", "text_accuracy"),
                "The fixed-text constraint has no matching required approved-copy entry.",
                "A required approved-copy entry preserving the original fixed text and exact match policy.",
                "A1 should verify the original fixed-text requirement, supply its approved-copy mapping, and request a new audit.",
                evidence=check.evidence,
            )
        elif check.status != "pass":
            add(
                "constraint_unknown" if check.status == "unknown" else "constraint_failed",
                check.status, "constraint", check.constraint_id, ("user_constraints",),
                "The user constraint is unconfirmed." if check.status == "unknown"
                else "The recorded user-constraint check failed.",
                "Candidate evidence that can be compared with this original constraint.",
                "A1 should retain the original constraint and verify candidate evidence before assigning a change.",
                evidence=check.evidence,
            )

    copy_by_id = {copy.copy_id: copy for copy in working.approved_copy or ()}
    for check in bound.text_checks:
        if check.status != "pass":
            add(
                "copy_unknown" if check.status == "unknown" else "copy_failed",
                check.status, "copy", check.copy_id, ("text_accuracy",),
                "Copy accuracy is unconfirmed." if check.status == "unknown"
                else "The recorded copy-accuracy check failed.",
                "A reliable transcription of this copy from the current candidate.",
                "A1 should compare the transcription with the approved copy and matching policy, then request a new audit after evidence or production changes.",
                evidence=check.observed_text,
                required=by_check["text_accuracy"].required and copy_by_id[check.copy_id].required,
            )
        if check.readability != "pass":
            add(
                "copy_readability_unknown" if check.readability == "unknown" else "copy_readability_failed",
                check.readability, "copy", check.copy_id, ("text_readability",),
                "Normal-viewing copy readability is unconfirmed." if check.readability == "unknown"
                else "The recorded copy-readability check failed.",
                "Readable visual evidence of this copy at normal viewing size.",
                "A1 should verify this copy in the current candidate before deciding whether to change layout or rendering.",
                evidence=check.observed_text,
            )

    for context, check_id, code in (
        (working.approved_copy, "text_accuracy", "approved_copy_missing"),
        (working.layout_context, "layout_geometry", "layout_context_missing"),
    ):
        if context is None:
            coverage = by_check[check_id]
            add(
                code, "unknown" if coverage.required else "not_assessed", "check", check_id, (check_id,),
                "The declared audit context for this check was not supplied.",
                "Current approved copy with IDs and matching policy." if check_id == "text_accuracy"
                else "Current declared layout elements, boxes, and copy references.",
                "A1 should supply and verify current context before requesting this check again.",
            )

    # Coverage retains checks absent from the object-level observations. A reason
    # may be useful human evidence, but its wording never determines a code/ID.
    represented = {check_id for item in items for check_id in item.check_ids if item.subject_kind != "execution"}
    for coverage in bound.coverage:
        if coverage.status == "pass" or coverage.check_id in represented:
            continue
        status = "not_assessed" if coverage.state == "not_assessed" else coverage.status
        add(
            "check_not_assessed" if status == "not_assessed" else "check_unknown" if status == "unknown" else "check_failed",
            status, "check", coverage.check_id, (coverage.check_id,),
            "This check was not assessed." if status == "not_assessed" else
            "This check lacks conclusive evidence." if status == "unknown" else "The recorded check failed.",
            "Check-specific evidence of the current candidate.",
            "A1 should compare the typed check and its preserved human reason with the current candidate, then obtain evidence before selecting a change.",
            evidence=coverage.reason,
        )

    # Reports combine model and policy uncertainties in one string tuple. Their
    # source and object cannot be recovered without parsing prose or guessing.
    for uncertainty in bound.uncertainties:
        add(
            "report_uncertainty_unlocated", "unknown", "report_uncertainty", None, (),
            "The report records an uncertainty with no separately typed source or object.",
            "Additional evidence identifying the affected requirement or check.",
            "A1 should inspect the preserved uncertainty and current candidate, identify its scope, and request a new audit when evidence is available.",
            evidence=uncertainty, required=True,
        )

    return DiagnosticsEnvelopeV1(
        sample_id=bound.sample_id, image_sha256=bound.image_sha256,
        input_fingerprint=input_fingerprint(working), decision=bound.decision,
        items=tuple(items), human_uncertainties=bound.uncertainties,
        coverage=bound.coverage, findings=bound.issues, calls=bound.calls,
    )

"""Turn evidence into a report; models never choose artifact IDs or routes."""

from __future__ import annotations

from .checks import compare_text
from .contracts import (
    ALL_CHECKS, AuditInputV1, AuditReportV3, CallRecordV2, ConstraintCheck, Coverage,
    Finding, KeywordCheck, ModelObservation, RepairHint, Status, TextCheck,
)
from .diagnostics import explain_unknowns
from .identity import input_fingerprint


def combine(statuses: list[Status]) -> Status:
    if "unknown" in statuses:
        return "unknown"
    return "fail" if "fail" in statuses else "pass"


def make_report(
    audit: AuditInputV1, observation: ModelObservation | None,
    program_findings: tuple[Finding, ...], *, model: str, live: bool,
    calls: tuple[CallRecordV2, ...], prompt_version: str, failure: str | None = None,
) -> AuditReportV3:
    issues = list(program_findings)
    if observation is None:
        keywords = tuple(KeywordCheck(keyword_id=k.keyword_id, status="unknown",
                                     evidence="No valid model observation.") for k in audit.keywords)
        constraints = tuple(ConstraintCheck(constraint_id=c.constraint_id, status="unknown",
                                           evidence="No valid model observation.") for c in audit.user_constraints)
        texts = tuple(TextCheck(copy_id=c.copy_id, observed_text=None, status="unknown",
                                readability="unknown") for c in audit.approved_copy or ())
        visual = {"text_readability": "unknown", "layout_quality": "unknown"}
    else:
        keywords = observation.keyword_checks
        constraints = observation.constraint_checks
        texts, text_findings = compare_text(audit, observation.text_checks)
        text_by_id = {t.copy_id: t for t in texts}
        literal_ids = {c.constraint_id for c in audit.user_constraints if c.name == 'required_text'}
        constraints = tuple(
            c.model_copy(update={
                'status': text_by_id['constraint:' + c.constraint_id].status
                if 'constraint:' + c.constraint_id in text_by_id else 'unknown',
                'evidence': 'Literal text constraint checked locally against the recorded transcription.',
            }) if c.constraint_id in literal_ids else c
            for c in constraints
        )
        issues.extend(text_findings)
        issues.extend(Finding(**i.model_dump(), source="model") for i in observation.issues)
        visual = {c.check_id: c.status for c in observation.visual_checks}
        for check in observation.visual_checks:
            if check.status == "fail":
                issues.append(Finding(
                    code=f"{check.check_id}_failed", severity="major", source="model",
                    evidence=check.evidence, suggestion="Inspect the visible problem before assigning a repair stage.",
                ))
        for check in texts:
            if check.readability == "fail":
                issues.append(Finding(
                    code="text_unreadable", severity="major", source="model", element_id=check.copy_id,
                    evidence=f"The model reports that copy item {check.copy_id!r} is difficult to read.",
                    suggestion="Inspect this text at normal viewing size before choosing a repair stage.",
                ))
    for check in keywords:
        if check.status == "fail":
            issues.append(Finding(code="keyword_missing", severity="blocking", source="model",
                                  evidence=f"{check.keyword_id}: {check.evidence}",
                                  suggestion="Restore a clear expression of the original keyword."))
    for check in constraints:
        if check.status == "fail":
            local = any(c.constraint_id == check.constraint_id and c.name == 'required_text' for c in audit.user_constraints)
            issues.append(Finding(code="constraint_violation", severity="blocking", source="program" if local else "model",
                                  evidence=f"{check.constraint_id}: {check.evidence}",
                                  suggestion="Satisfy the original user constraint."))

    required_copy = {c.copy_id for c in audit.approved_copy or () if c.required}
    text_status = combine([c.status for c in texts if c.copy_id in required_copy])
    if "text_accuracy" not in audit.required_checks:
        text_status = combine([c.status for c in texts])
    missing_required_text = any(
        c.name == "required_text" and not any(
            item.required and item.text == c.value for item in audit.approved_copy or ()
        ) for c in audit.user_constraints
    )
    if missing_required_text:
        text_status = "unknown"
    statuses = {
        "keyword_alignment": combine([c.status for c in keywords]),
        "user_constraints": combine([c.status for c in constraints]),
        "text_readability": combine([visual["text_readability"], *(c.readability for c in texts)]),
        "layout_quality": visual["layout_quality"],
        "text_accuracy": text_status,
        "layout_geometry": "fail" if program_findings else "pass",
    }
    coverage_items = []
    for check_id in ALL_CHECKS:
        required = check_id in audit.required_checks
        absent = (check_id == "text_accuracy" and audit.approved_copy is None) or (
            check_id == "layout_geometry" and audit.layout_context is None
        )
        if absent:
            status = "unknown" if required else None
            state = "unknown" if required else "not_assessed"
            reason = "Required context is missing." if required else "Optional context was not supplied."
        else:
            status = statuses[check_id]
            state = "unknown" if status == "unknown" else "assessed"
            reason = "Evidence is insufficient." if state == "unknown" else "Based on the recorded checks."
        coverage_items.append(Coverage(check_id=check_id, required=required, state=state,
                                       status=status, reason=reason))

    coverage, uncertainties = explain_unknowns(
        audit, observation, keywords, constraints, texts, tuple(coverage_items), failure=failure,
    )

    incomplete = bool(uncertainties) or any(c.required and c.status == "unknown" for c in coverage)
    if any(c.required and c.status == "fail" for c in coverage) or any(
        item.severity in ("blocking", "major") for item in issues
    ):
        decision = "fail"
    elif incomplete:
        decision = "unknown"
    else:
        decision = "pass"
    # Only declared invalid geometry establishes a repair stage in this first version.
    # A visual symptom alone does not establish an asset/rendering/brief root cause.
    hint = None
    if (decision == "fail" and not incomplete and program_findings
            and not any(c.state == "unknown" for c in coverage)
            and not any(i.severity == "blocking" for i in issues)):
        issue = program_findings[0]
        hint = RepairHint(stage="layout", element_id=issue.element_id, reason=issue.evidence,
                          preserve="Preserve the original requirements, copy and existing assets.")
    return AuditReportV3(
        sample_id=audit.sample_id, image_sha256=audit.image.sha256, decision=decision,
        input_fingerprint=input_fingerprint(audit),
        keyword_checks=keywords, constraint_checks=constraints, text_checks=texts,
        coverage=tuple(coverage), issues=tuple(issues), uncertainties=tuple(uncertainties),
        repair_hint=hint, model=model, prompt_version=prompt_version,
        execution_mode="live" if live else "fake", calls=calls,
        vision_calls=sum(c.live for c in calls),
    )

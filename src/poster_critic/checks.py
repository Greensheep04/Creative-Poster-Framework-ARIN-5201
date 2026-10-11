"""Deterministic evidence checks. No model or framework imports."""

from __future__ import annotations

import unicodedata
import json
import re

from pydantic import ValidationError

from .contracts import AuditInputV1, CopyItem, Finding, ModelObservation, TextCheck
from .errors import ResponseError

_VIEWPOINT = re.compile(r"俯拍|仰拍|俯视|仰视|俯瞰|仰望|低机位|高机位|low[- ]angle|high[- ]angle|bird['’]s[- ]eye", re.IGNORECASE)


def viewpoint_requested(audit: AuditInputV1) -> bool:
    requirements = [k.text for k in audit.keywords]
    requirements.extend(json.dumps(c.model_dump(mode='json'), ensure_ascii=False) for c in audit.user_constraints if c.name != 'required_text')
    return any(_VIEWPOINT.search(text) for text in requirements)


def _exact_ids(actual: list[str], expected: set[str], label: str) -> None:
    if len(actual) != len(expected) or set(actual) != expected:
        raise ResponseError(f"{label} must cover each expected ID exactly once")


def known_element_ids(audit: AuditInputV1) -> frozenset[str]:
    ids = {c.copy_id for c in audit.approved_copy or ()}
    if audit.layout_context is not None:
        ids.update(e.element_id for e in audit.layout_context.elements)
    return frozenset(ids)


def matching_text(text: str, item: CopyItem) -> str:
    value = unicodedata.normalize("NFC", text)
    if item.match_policy == "typographic":
        # Presentation only: never fix spelling, numbers, homoglyphs or zero-width text.
        value = value.translate(str.maketrans({
            "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
            "“": '"', "”": '"', "‘": "'", "’": "'",
        }))
        value = " ".join(value.split()).casefold()
    return value


def text_matches(observed: str, item: CopyItem) -> bool:
    return matching_text(observed, item) == matching_text(item.text, item)


def parse_observation(raw: str, audit: AuditInputV1, *, blind_copy: bool = False) -> ModelObservation:
    try:
        observation = ModelObservation.model_validate_json(raw)
    except ValidationError as exc:
        errors = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'response'}: {e['msg']}"
            for e in exc.errors(include_input=False, include_url=False)[:5]
        )
        raise ResponseError(errors) from exc
    _exact_ids([c.keyword_id for c in observation.keyword_checks],
               {c.keyword_id for c in audit.keywords}, "keyword checks")
    _exact_ids([c.constraint_id for c in observation.constraint_checks],
               {c.constraint_id for c in audit.user_constraints}, "constraint checks")
    _exact_ids([c.copy_id for c in observation.text_checks],
               {c.copy_id for c in audit.approved_copy or ()}, "text checks")
    _exact_ids([c.check_id for c in observation.visual_checks],
               {"text_readability", "layout_quality"}, "visual checks")
    known_elements = known_element_ids(audit)
    for issue in observation.issues:
        if issue.element_id is not None and issue.element_id not in known_elements:
            raise ResponseError("issue refers to an unknown element ID; use null if unlocated")
    if not viewpoint_requested(audit):
        evidence = [c.evidence for c in (*observation.keyword_checks, *observation.constraint_checks,
                                        *observation.visual_checks, *observation.issues)]
        if any(_VIEWPOINT.search(text) for text in evidence):
            raise ResponseError("unrequested viewpoint claim in evidence")
    revised_text = []
    for check in observation.text_checks:
        if check.observed_text is None and check.status != "unknown":
            raise ResponseError("unreadable observed_text requires unknown accuracy status")
        expected = next(c for c in audit.approved_copy or () if c.copy_id == check.copy_id)
        if blind_copy:
            if check.status != 'unknown':
                raise ResponseError('blind copy observations require unknown accuracy before local comparison')
            if check.observed_text is not None:
                check = check.model_copy(update={'status': 'pass' if text_matches(check.observed_text, expected) else 'fail'})
            revised_text.append(check)
            continue
        if check.status == "fail" and check.observed_text is not None and (
            text_matches(check.observed_text, expected)
        ):
            raise ResponseError("text accuracy failure contradicts the declared matching policy")
        revised_text.append(check)
    return observation.model_copy(update={'text_checks': tuple(revised_text)})


def geometry_findings(audit: AuditInputV1) -> tuple[Finding, ...]:
    findings = []
    for element in audit.layout_context.elements if audit.layout_context else ():
        x0, y0, x1, y1 = element.bbox_xyxy
        if not (0 <= x0 < x1 <= 720 and 0 <= y0 < y1 <= 1280):
            findings.append(Finding(
                code="layout_out_of_bounds", severity="major", element_id=element.element_id,
                bbox_xyxy=element.bbox_xyxy, source="program",
                evidence=f"Declared box {element.bbox_xyxy} is invalid on the 720x1280 canvas.",
                suggestion="Place this element within the canvas; preserve its text and assets.",
            ))
    return tuple(findings)


def compare_text(
    audit: AuditInputV1, checks: tuple[TextCheck, ...]
) -> tuple[tuple[TextCheck, ...], tuple[Finding, ...]]:
    expected = {item.copy_id: item for item in audit.approved_copy or ()}
    revised = []
    findings = []
    for check in checks:
        item = expected[check.copy_id]
        mismatch = check.observed_text is not None and not text_matches(check.observed_text, item)
        # Unknown observations remain unknown; readable literal mismatches cannot pass.
        if mismatch and check.status != "unknown":
            check = check.model_copy(update={"status": "fail"})
        revised.append(check)
        if check.status == "fail":
            findings.append(Finding(
                code="text_mismatch", severity="blocking" if item.required else "minor",
                element_id=item.copy_id, source="program" if mismatch else "model",
                evidence=f"Expected {item.text!r}; observed {check.observed_text!r} (policy={item.match_policy}).",
                suggestion="Check the copy against the original requirement before changing production.",
            ))
    return tuple(revised), tuple(findings)

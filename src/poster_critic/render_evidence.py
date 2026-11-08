"""A4-owned renderer facts, with caller-owned decoding and source authority.

This is a normalized A4 contract, not a schema imposed on the Renderer. A real
integration must supply a decoder, registered-byte reader and current-source
query. Hash matching and a named authority prove internal/source consistency
under that caller's trust policy; they do not authenticate an arbitrary client.
Even a clean manifest cannot prove that text in the final pixels is readable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
from typing import Annotated, Literal, Protocol, Self

from PIL import Image
from pydantic import Field, StringConstraints, ValidationError, field_validator, model_validator

from .checks import text_matches
from .contracts import AuditInputV1, BBox, Contract, Finding, RepairHint, Text, unique
from .dispatch import _validated_input, _validate_image_bytes
from .errors import InputError
from .identity import input_fingerprint

Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
MAX_RENDER_EVIDENCE_BYTES = 2 * 1024 * 1024


class RenderSourceRevision(Contract):
    source_id: Text
    revision: Text


class RenderEvidenceReference(Contract):
    """Reference resolved by the caller's registered-byte reader."""

    evidence_id: Text
    revision: Text
    sha256: Digest


class RenderElementV1(Contract):
    element_id: Text
    copy_id: Text | None = None
    spec_bbox_xyxy: BBox
    ink_bbox_xyxy: BBox | None = None
    draw_state: Literal["drawn", "not_drawn", "unknown"] = "unknown"
    final_text: str | None = None
    final_lines: tuple[str, ...] | None = None
    # Soft wrapping contributes no character. Explicit hard breaks contribute LF.
    line_breaks: tuple[Literal["soft", "hard"], ...] | None = None
    copy_extent: Literal["complete", "fragment", "unknown"] = "unknown"
    font_sha256: Digest | None = None
    font_size_px: Annotated[float, Field(gt=0, le=4096)] | None = None
    font_weight: Annotated[int, Field(ge=1, le=1000)] | None = None
    missing_glyphs: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def consistent_text_facts(self) -> Self:
        if self.ink_bbox_xyxy is not None:
            x0, y0, x1, y1 = self.ink_bbox_xyxy
            if x0 >= x1 or y0 >= y1:
                raise ValueError("ink bounds must have positive area; use null if unavailable")
        if self.draw_state == "not_drawn" and self.ink_bbox_xyxy is not None:
            raise ValueError("an undrawn element cannot declare ink")
        if self.final_lines is None:
            if self.line_breaks is not None:
                raise ValueError("line breaks require final lines")
        elif self.line_breaks is not None:
            if not self.final_lines or len(self.line_breaks) != len(self.final_lines) - 1:
                raise ValueError("each final line boundary must be explicitly classified")
            combined = self.final_lines[0]
            for kind, line in zip(self.line_breaks, self.final_lines[1:], strict=True):
                combined += ("\n" if kind == "hard" else "") + line
            if self.final_text is None or combined != self.final_text:
                raise ValueError("final lines must reconstruct the exact submitted text")
        if self.missing_glyphs is not None:
            unique(list(self.missing_glyphs), "missing glyph characters")
            if any(len(char) != 1 for char in self.missing_glyphs):
                raise ValueError("missing glyph entries must be individual Unicode characters")
            if self.missing_glyphs and (self.final_text is None or any(
                char not in self.final_text for char in self.missing_glyphs
            )):
                raise ValueError("missing glyph characters must occur in submitted text")
        if self.copy_id is None and any(value is not None for value in (
            self.final_text, self.final_lines, self.line_breaks, self.font_sha256,
            self.font_size_px, self.font_weight, self.missing_glyphs,
        )):
            raise ValueError("text facts require an explicit approved-copy reference")
        return self


class RenderOverlapRule(Contract):
    first_element_id: Text
    second_element_id: Text

    @model_validator(mode="after")
    def different_elements(self) -> Self:
        if self.first_element_id == self.second_element_id:
            raise ValueError("overlap rules must name two different elements")
        return self


class RenderEvidenceV1(Contract):
    schema_version: Literal[1] = 1
    evidence_kind: Literal["renderer_manifest"] = "renderer_manifest"
    sample_id: Text
    evidence_id: Text
    source_revision: Text
    producer_id: Text
    renderer_version: Text
    font_config_revision: Text
    source_revisions: Annotated[tuple[RenderSourceRevision, ...], Field(min_length=1)]
    parent_image_sha256: Digest
    input_fingerprint: Digest
    canvas_width: Literal[720] = 720
    canvas_height: Literal[1280] = 1280
    color_mode: Literal["1", "L", "P", "RGB", "RGBA", "CMYK"]
    elements: tuple[RenderElementV1, ...]
    overlap_policy: Literal["declared", "unavailable"] = "unavailable"
    allowed_overlaps: tuple[RenderOverlapRule, ...] = ()

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("render evidence version must be an integer")
        return value

    @field_validator("canvas_width", "canvas_height", mode="before")
    @classmethod
    def integer_canvas(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("render canvas dimensions must be integers")
        return value

    @model_validator(mode="after")
    def distinct_references(self) -> Self:
        unique([item.element_id for item in self.elements], "render element IDs")
        unique([item.source_id for item in self.source_revisions], "render source IDs")
        pairs = [tuple(sorted((item.first_element_id, item.second_element_id)))
                 for item in self.allowed_overlaps]
        if len(pairs) != len(set(pairs)):
            raise ValueError("allowed overlaps must contain unique unordered pairs")
        if self.overlap_policy == "unavailable" and self.allowed_overlaps:
            raise ValueError("unavailable overlap policy cannot declare exceptions")
        return self


class RenderEvidenceDecoder(Protocol):
    def __call__(self, payload: bytes) -> RenderEvidenceV1: ...


@dataclass(frozen=True, slots=True)
class CurrentRenderEvidenceSource:
    """Caller-owned query result, not something asserted by the input JSON.

    ``producer_id`` identifies the renderer the caller permits for this source.
    Revisions include the authoritative spec/asset/config dependencies needed by
    its decoder. The caller must define which query is authoritative.
    """

    authority: str
    producer_id: str
    renderer_version: str
    font_config_revision: str
    reference: RenderEvidenceReference
    source_revisions: tuple[RenderSourceRevision, ...]
    parent_image_sha256: str
    input_fingerprint: str


@dataclass(frozen=True, slots=True)
class RenderEvidenceMapping:
    source_format: str
    reference: RenderEvidenceReference
    read: Callable[[RenderEvidenceReference], bytes]
    decode: RenderEvidenceDecoder
    current_source: Callable[[], CurrentRenderEvidenceSource]


@dataclass(frozen=True, slots=True)
class BoundRenderEvidence:
    """A4 private binding re-read and revalidated by analysis, not a QC verdict."""

    evidence: RenderEvidenceV1
    authority: str
    reference: RenderEvidenceReference
    _mapping: RenderEvidenceMapping = field(repr=False)
    _current: CurrentRenderEvidenceSource = field(repr=False)
    _payload: bytes = field(repr=False)


class RenderEvidenceCheck(Contract):
    check_id: Literal["source_binding", "render_geometry", "render_text", "glyph_coverage", "overlap_policy"]
    status: Literal["pass", "fail", "unknown"]
    reason: Text


class RenderEvidenceAnalysis(Contract):
    schema_version: Literal[1] = 1
    evidence_kind: Literal["renderer_manifest"] = "renderer_manifest"
    quality_status: Literal["fail", "unknown"]
    pixel_verification: Literal["not_performed"] = "not_performed"
    authority: Text | None = None
    evidence_id: Text | None = None
    evidence_revision: Text | None = None
    checks: tuple[RenderEvidenceCheck, ...]
    findings: tuple[Finding, ...]
    repair_hints: tuple[RepairHint, ...]
    uncertainties: tuple[Text, ...]
    recommended_actions: tuple[Text, ...]


def decode_render_evidence_json(payload: bytes) -> RenderEvidenceV1:
    """Decoder for the A4 normalized format, not for an unknown vendor schema."""
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_RENDER_EVIDENCE_BYTES:
        raise InputError("render evidence must contain bounded non-empty bytes")
    try:
        return RenderEvidenceV1.model_validate_json(payload)
    except (ValidationError, ValueError, TypeError) as exc:
        raise InputError("invalid normalized A4 render evidence") from exc


def _revalidate(value: object, cls: type[Contract], label: str):
    if not isinstance(value, cls):
        raise InputError(f"{label} requires {cls.__name__}")
    try:
        return cls.model_validate_json(value.model_dump_json())
    except (ValidationError, ValueError, TypeError, AttributeError) as exc:
        raise InputError(f"invalid {label}") from exc


def _current(mapping: RenderEvidenceMapping) -> CurrentRenderEvidenceSource:
    if (not isinstance(mapping, RenderEvidenceMapping) or not isinstance(mapping.source_format, str)
            or not mapping.source_format.strip() or not all(callable(value) for value in (
                mapping.read, mapping.decode, mapping.current_source,
            ))):
        raise InputError("render evidence requires an explicit reader, decoder and source query")
    current = mapping.current_source()
    if not isinstance(current, CurrentRenderEvidenceSource):
        raise InputError("render current-source query must return CurrentRenderEvidenceSource")
    if any(not isinstance(value, str) or not value.strip() for value in (
        current.authority, current.producer_id, current.renderer_version, current.font_config_revision,
    )):
        raise InputError("render evidence requires a named authority, trusted producer and renderer/font versions")
    reference = _revalidate(current.reference, RenderEvidenceReference, "current render reference")
    if not isinstance(current.source_revisions, tuple):
        raise InputError("current render dependencies require an explicit tuple")
    revisions = tuple(_revalidate(item, RenderSourceRevision, "current render dependency")
                      for item in current.source_revisions)
    if not revisions or len({item.source_id for item in revisions}) != len(revisions):
        raise InputError("current render dependencies mix source revisions")
    if reference != _revalidate(mapping.reference, RenderEvidenceReference, "render reference"):
        raise InputError("render evidence reference is stale")
    return CurrentRenderEvidenceSource(current.authority, current.producer_id, current.renderer_version,
                                       current.font_config_revision, reference, revisions,
                                       current.parent_image_sha256, current.input_fingerprint)


def _revision_pairs(revisions: tuple[RenderSourceRevision, ...]) -> frozenset[tuple[str, str]]:
    return frozenset((item.source_id, item.revision) for item in revisions)


def _bind(audit: AuditInputV1, mapping: RenderEvidenceMapping) -> BoundRenderEvidence:
    audit = _validated_input(audit)
    _validate_image_bytes(audit)
    before = _current(mapping)
    fingerprint = input_fingerprint(audit)
    if (before.parent_image_sha256 != audit.image.sha256 or before.input_fingerprint != fingerprint):
        raise InputError("current render source does not identify this complete audit input")
    payload = mapping.read(before.reference)
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_RENDER_EVIDENCE_BYTES:
        raise InputError("render evidence reader must return bounded non-empty bytes")
    if sha256(payload).hexdigest() != before.reference.sha256:
        raise InputError("render evidence bytes do not match the registered reference")
    evidence = _revalidate(mapping.decode(payload), RenderEvidenceV1, "decoded render evidence")
    if (evidence.evidence_id != before.reference.evidence_id
            or evidence.source_revision != before.reference.revision
            or evidence.producer_id != before.producer_id
            or evidence.renderer_version != before.renderer_version
            or evidence.font_config_revision != before.font_config_revision
            or evidence.parent_image_sha256 != audit.image.sha256
            or evidence.input_fingerprint != fingerprint
            or evidence.sample_id != audit.sample_id
            or _revision_pairs(evidence.source_revisions) != _revision_pairs(before.source_revisions)):
        raise InputError("render evidence does not match its current source, producer and audit binding")
    with Image.open(BytesIO(audit.image.data)) as image:
        if evidence.color_mode != image.mode:
            raise InputError("render canvas color mode does not match the actual image")
    known = {item.element_id: item for item in audit.layout_context.elements} if audit.layout_context else {}
    copies = {item.copy_id for item in audit.approved_copy or ()}
    for item in evidence.elements:
        if item.element_id not in known or item.copy_id != known[item.element_id].copy_id:
            raise InputError("render element/copy reference does not match the current layout")
        if item.copy_id is not None and item.copy_id not in copies:
            raise InputError("render copy reference does not identify approved copy")
    for rule in evidence.allowed_overlaps:
        if rule.first_element_id not in known or rule.second_element_id not in known:
            raise InputError("overlap rule refers to an unknown layout element")
    after = _current(mapping)
    if before != after:
        raise InputError("render evidence source changed while it was being read")
    return BoundRenderEvidence(evidence, before.authority, before.reference, mapping, before, payload)


def read_render_evidence(audit: AuditInputV1, mapping: RenderEvidenceMapping) -> BoundRenderEvidence:
    """Read explicitly registered source bytes and bind current source revisions."""
    return _bind(audit, mapping)


def validate_render_evidence(audit: AuditInputV1, bound: BoundRenderEvidence) -> BoundRenderEvidence:
    """Check actual source bytes and caller authority again immediately before use."""
    if not isinstance(bound, BoundRenderEvidence):
        raise InputError("renderer analysis requires bound evidence; client declarations are insufficient")
    checked = _bind(audit, bound._mapping)
    existing = _revalidate(bound.evidence, RenderEvidenceV1, "bound render evidence")
    if (checked.evidence != existing or checked.authority != bound.authority
            or checked.reference != bound.reference or checked._current != bound._current
            or checked._payload != bound._payload):
        raise InputError("bound render evidence changed or is stale")
    return checked


def _inside(inner: BBox, outer: BBox) -> bool:
    return outer[0] <= inner[0] < inner[2] <= outer[2] and outer[1] <= inner[1] < inner[3] <= outer[3]


def _intersection(first: BBox, second: BBox) -> BBox | None:
    box = (max(first[0], second[0]), max(first[1], second[1]),
           min(first[2], second[2]), min(first[3], second[3]))
    return box if box[0] < box[2] and box[1] < box[3] else None


def analyze_render_evidence(audit: AuditInputV1, bound: BoundRenderEvidence | None) -> RenderEvidenceAnalysis:
    """Produce renderer-only facts; a clean manifest never makes QC pass.

    Missing evidence is an explicit unknown. Invalid, stale or unbound evidence
    is an input/provenance error, rather than a quality unknown for the poster.
    """
    audit = _validated_input(audit)
    check_ids = ("source_binding", "render_geometry", "render_text", "glyph_coverage", "overlap_policy")
    pixel_note = "Renderer facts do not establish pixel text readability, contrast, occlusion or visual quality."
    if bound is None:
        return RenderEvidenceAnalysis(
            quality_status="unknown",
            checks=tuple(RenderEvidenceCheck(check_id=check, status="unknown", reason="Current renderer evidence is unavailable.")
                         for check in check_ids),
            findings=(), repair_hints=(), uncertainties=("Current renderer evidence is unavailable.", pixel_note),
            recommended_actions=("Provide registered renderer bytes, a documented decoder and an authoritative current-source query.",),
        )
    checked = validate_render_evidence(audit, bound)
    evidence = checked.evidence
    expected_elements = {item.element_id: item for item in audit.layout_context.elements} if audit.layout_context else {}
    expected_copy = {item.copy_id: item for item in audit.approved_copy or ()}
    rendered = {item.element_id: item for item in evidence.elements}
    findings: list[Finding] = []
    hints: list[RepairHint] = []
    uncertain = [pixel_note]
    actions = ["Check the actual final image independently for readability and visual quality."]
    states: dict[str, list[str]] = {check: [] for check in check_ids}

    def finding(check: str, code: str, item: RenderElementV1, reason: str, suggestion: str,
                *, severity: Literal["blocking", "major", "minor"] = "major", box: BBox | None = None) -> None:
        states[check].append("fail")
        findings.append(Finding(code=code, severity=severity, element_id=item.element_id,
                                bbox_xyxy=box, source="program", evidence=reason, suggestion=suggestion))
        hints.append(RepairHint(stage="render", element_id=item.element_id, reason=reason,
                                preserve="Preserve approved wording, asset identities and unrelated elements; A1 chooses the repair."))

    missing_elements = sorted(set(expected_elements) - set(rendered))
    if missing_elements or not expected_elements:
        states["render_geometry"].append("unknown")
        uncertain.append("Renderer coverage is incomplete for layout elements: " + (", ".join(missing_elements) or "layout context unavailable"))
        actions.append("Provide actual drawing facts for every current layout element.")
    for item in evidence.elements:
        expected = expected_elements[item.element_id]
        if item.spec_bbox_xyxy != expected.bbox_xyxy:
            finding("render_geometry", "render_spec_box_mismatch", item,
                    f"Renderer Spec box {item.spec_bbox_xyxy} differs from the current layout {expected.bbox_xyxy}.",
                    "Verify the current Spec revision and renderer coordinates before changing layout.")
        if item.draw_state == "not_drawn":
            finding("render_geometry", "render_element_not_drawn", item,
                    "The renderer reports that the current layout element was not drawn.",
                    "Check the renderer's draw result and source binding; preserve this element's content.")
        elif item.draw_state == "unknown" or item.ink_bbox_xyxy is None:
            states["render_geometry"].append("unknown")
            uncertain.append(f"{item.element_id}: actual drawing state or ink bounds are unavailable.")
        else:
            if not _inside(item.ink_bbox_xyxy, (0.0, 0.0, 720.0, 1280.0)):
                finding("render_geometry", "render_ink_out_of_canvas", item,
                        f"Actual renderer ink bounds {item.ink_bbox_xyxy} extend outside the 720x1280 canvas.",
                        "Check the actual drawing position and measured ink bounds; preserve wording and assets.", box=item.ink_bbox_xyxy)
            if not _inside(item.ink_bbox_xyxy, expected.bbox_xyxy):
                finding("render_geometry", "render_ink_out_of_spec_box", item,
                        f"Actual renderer ink bounds {item.ink_bbox_xyxy} do not fit the current declared box {expected.bbox_xyxy}.",
                        "Check text size, line wrapping and draw coordinates against the declared box; do not rewrite copy.", box=item.ink_bbox_xyxy)
        if item.copy_id is None:
            continue
        copy = expected_copy[item.copy_id]
        if item.draw_state == "not_drawn":
            finding("render_text", "render_copy_not_drawn", item,
                    f"Approved copy {copy.copy_id!r} was not drawn according to the renderer.",
                    "Restore the approved copy through A1 after checking renderer failures.",
                    severity="blocking" if copy.required else "minor")
        elif (item.final_text is None or item.copy_extent != "complete"
              or item.final_lines is None or item.line_breaks is None):
            states["render_text"].append("unknown")
            uncertain.append(f"{item.element_id}/{item.copy_id}: complete final renderer text and line-boundary facts are unavailable.")
            if "Provide complete submitted text, final lines and explicit soft/hard line boundaries." not in actions:
                actions.append("Provide complete submitted text, final lines and explicit soft/hard line boundaries.")
        elif not text_matches(item.final_text, copy):
            finding("render_text", "render_text_mismatch", item,
                    f"Expected {copy.text!r}; renderer submitted {item.final_text!r} (policy={copy.match_policy}).",
                    "Verify final renderer text against approved copy; preserve the original requirement.",
                    severity="blocking" if copy.required else "minor")
        if (item.font_sha256 is None or item.font_size_px is None or item.font_weight is None
                or item.missing_glyphs is None or item.final_text is None):
            states["glyph_coverage"].append("unknown")
            uncertain.append(f"{item.element_id}/{item.copy_id}: actual font identity, size/weight or measured glyph coverage are unavailable.")
            if "Provide actual font identity and measured glyph coverage; do not infer coverage from requested font names." not in actions:
                actions.append("Provide actual font identity and measured glyph coverage; do not infer coverage from requested font names.")
        elif item.missing_glyphs:
            finding("glyph_coverage", "render_missing_glyphs", item,
                    f"The renderer reports unavailable glyphs {item.missing_glyphs!r} in font {item.font_sha256}.",
                    "Select an explicitly approved font covering these characters; preserve exact copy and verify font identity.",
                    severity="blocking" if copy.required else "minor")

    represented_copies = {item.copy_id for item in evidence.elements if item.copy_extent == "complete"}
    if not expected_copy or set(expected_copy) - represented_copies:
        states["render_text"].append("unknown")
        states["glyph_coverage"].append("unknown")
    if evidence.overlap_policy == "unavailable":
        states["overlap_policy"].append("unknown")
        uncertain.append("Intentional overlap rules are unavailable; geometric intersections cannot be classified automatically.")
        actions.append("Provide the current Spec's explicit allowed overlap rules.")
    else:
        allowed = {frozenset((rule.first_element_id, rule.second_element_id)) for rule in evidence.allowed_overlaps}
        if missing_elements or not expected_elements or any(
            item.draw_state == "unknown" or (item.draw_state == "drawn" and item.ink_bbox_xyxy is None)
            for item in evidence.elements
        ):
            states["overlap_policy"].append("unknown")
        items = tuple(evidence.elements)
        for index, first in enumerate(items):
            for second in items[index + 1:]:
                if (first.draw_state != "drawn" or second.draw_state != "drawn"
                        or first.ink_bbox_xyxy is None or second.ink_bbox_xyxy is None):
                    continue
                overlap = _intersection(first.ink_bbox_xyxy, second.ink_bbox_xyxy)
                if overlap is not None and frozenset((first.element_id, second.element_id)) not in allowed:
                    finding("overlap_policy", "render_unapproved_overlap", first,
                            f"Renderer ink for {first.element_id!r} and {second.element_id!r} intersects at {overlap}; the current overlap policy does not allow this pair.",
                            "Have A1 check the explicit overlap intention and drawing order before changing positions.", box=overlap)
    reasons = {
        "source_binding": "Registered bytes match the current authority, producer, source revisions and complete input.",
        "render_geometry": "Renderer ink/Spec facts are internally consistent with the declared geometry; this is not pixel verification.",
        "render_text": "Complete final-line text matches the approved copy under its declared policy; this is not a visual transcription.",
        "glyph_coverage": "Renderer font identities and declared glyph coverage are complete; this does not establish readable pixels.",
        "overlap_policy": "Measured intersections conform to the explicit current overlap policy; readability still requires image inspection.",
    }
    checks = []
    for check in check_ids:
        status = "fail" if "fail" in states[check] else "unknown" if "unknown" in states[check] else "pass"
        reason = reasons[check] if status == "pass" else (
            "Confirmed renderer evidence contradicts this check; inspect the attached findings." if status == "fail"
            else "Required renderer facts or authoritative policy are missing; do not infer a pass."
        )
        checks.append(RenderEvidenceCheck(check_id=check, status=status, reason=reason))
        if status == "unknown":
            uncertain.append(f"{check}: required renderer facts or policy are missing.")
    return RenderEvidenceAnalysis(
        quality_status="fail" if any(item.severity in ("blocking", "major") for item in findings) else "unknown", authority=checked.authority,
        evidence_id=evidence.evidence_id, evidence_revision=evidence.source_revision,
        checks=tuple(checks), findings=tuple(findings), repair_hints=tuple(hints),
        uncertainties=tuple(uncertain), recommended_actions=tuple(actions),
    )

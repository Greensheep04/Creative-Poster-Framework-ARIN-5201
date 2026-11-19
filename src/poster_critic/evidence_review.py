"""Opt-in supplementary renderer/OCR checks. Never replace a primary audit.

Evidence can add confirmed defects or release-blocking gaps. It never converts
an unknown primary judgment into pass, nor sends approved copy to an OCR tool.
"""
from __future__ import annotations

from typing import Annotated, Literal, Self
import os
import stat
import json

from pydantic import Field, field_validator, model_validator

from .checks import text_matches
from .contracts import AuditInputV1, AuditReportV3, CheckId, Contract, Finding, RepairHint, Text
from .dispatch import _bound_report, _validate_image_bytes, _validated_input
from .errors import InputError
from .evidence import Digest, crop_evidence
from .identity import input_fingerprint
from .macos_vision import VisionAttemptV1, VisionBudget, VisionConfigV1, VisionOCR
from .ocr import OCRAttemptV1, OCRBudget, TesseractConfigV1, TesseractOCR
from .render_evidence import BoundRenderEvidence, RenderEvidenceAnalysis, analyze_render_evidence
from .render_evidence import validate_render_evidence


class OCRRegionV1(Contract):
    copy_id: Text
    bbox_xyxy: tuple[int, int, int, int]
    required_for_release: bool = True
    minimum_confidence: Annotated[float, Field(ge=0, le=1)] = 0.8

    @model_validator(mode='after')
    def positive_region(self) -> Self:
        x0, y0, x1, y1 = self.bbox_xyxy
        if not 0 <= x0 < x1 <= 720 or not 0 <= y0 < y1 <= 1280:
            raise ValueError('OCR region must be inside the unmodified final canvas')
        return self


class EvidenceGapV1(Contract):
    check_id: CheckId
    subject_id: Text | None
    code: Text
    required_for_release: bool
    doubt: Text
    missing_evidence: Text
    suggestion: Text


class OCRReviewRecordV1(Contract):
    copy_id: Text
    region_bbox_xyxy: tuple[int, int, int, int]
    backend: Literal['tesseract', 'apple_vision']
    attempt: OCRAttemptV1 | VisionAttemptV1


class RenderReviewBindingV1(Contract):
    evidence_id: Text
    revision: Text
    authority: Text
    image_sha256: Digest
    input_fingerprint: Digest
    source_sha256: Digest


class SupplementalReviewV1(Contract):
    schema_version: Literal[1] = 1
    kind: Literal['a4_supplemental_review'] = 'a4_supplemental_review'
    sample_id: Text
    image_sha256: Digest
    input_fingerprint: Digest
    renderer: RenderEvidenceAnalysis | None
    renderer_binding: RenderReviewBindingV1 | None = None
    ocr: tuple[OCRReviewRecordV1, ...]
    findings: tuple[Finding, ...]
    gaps: tuple[EvidenceGapV1, ...]
    proposed_hints: tuple[RepairHint, ...]

    @field_validator('schema_version', mode='before')
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError('supplementary version must be integer')
        return value


class SecondaryReviewConfigV1(Contract):
    schema_version: Literal[1] = 1
    backend: Literal['tesseract', 'apple_vision']
    tesseract: TesseractConfigV1 | None = None
    apple_vision: VisionConfigV1 | None = None
    regions: Annotated[tuple[OCRRegionV1, ...], Field(min_length=1, max_length=8)]
    max_tool_attempts: Annotated[int, Field(ge=1, le=8)] = 2
    tool_timeout_seconds: Annotated[float, Field(gt=0, le=300)] = 30.0

    @field_validator('schema_version', mode='before')
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError('secondary policy version must be integer')
        return value

    @model_validator(mode='after')
    def explicit_backend(self) -> Self:
        if (self.backend == 'tesseract') != (self.tesseract is not None) or (self.backend == 'apple_vision') != (self.apple_vision is not None):
            raise ValueError('select exactly the declared OCR backend configuration')
        if len({x.copy_id for x in self.regions}) != len(self.regions):
            raise ValueError('secondary policy must have unique copy regions')
        return self


def read_secondary_review(path) -> 'EvidenceReviewer':
    try:
        from pathlib import Path
        path = Path(path)
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError
            raw = handle.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError
                result[key] = value
            return result
        json.loads(raw, object_pairs_hook=pairs)
        config = SecondaryReviewConfigV1.model_validate_json(raw)
        backend = TesseractOCR(config.tesseract) if config.backend == 'tesseract' else VisionOCR(config.apple_vision)
        return EvidenceReviewer(ocr=backend, regions=config.regions,
            max_tool_attempts=config.max_tool_attempts, tool_timeout_seconds=config.tool_timeout_seconds)
    except (OSError, ValueError, TypeError) as exc:
        raise InputError('invalid explicit secondary review policy; no fallback selected') from None


def _checked_attempt(audit: AuditInputV1, crop, attempt, *, backend: str,
                     config_sha256: str | None = None):
    cls = VisionAttemptV1 if backend == 'apple_vision' else OCRAttemptV1
    if not isinstance(attempt, cls):
        raise InputError('OCR attempt does not match the selected backend')
    try:
        attempt = cls.model_validate_json(attempt.model_dump_json())
    except (ValueError, TypeError, AttributeError) as exc:
        raise InputError('invalid bound OCR attempt contract') from None
    if (attempt.sample_id != audit.sample_id or attempt.input_fingerprint != input_fingerprint(audit)
            or (config_sha256 is not None and attempt.config_sha256 != config_sha256)):
        raise InputError('OCR attempt does not match the current input and selected configuration')
    if isinstance(attempt, OCRAttemptV1) and (
        attempt.parent_image_sha256 != audit.image.sha256
        or attempt.crop_sha256 not in (None, crop.sha256)
    ):
        raise InputError('OCR attempt does not match the current parent and selected region')
    observation = attempt.observation
    if observation is not None and (
        observation.sample_id != audit.sample_id or observation.parent_image_sha256 != audit.image.sha256
        or observation.input_fingerprint != input_fingerprint(audit) or observation.crop_sha256 != crop.sha256
        or observation.crop_bbox_xyxy != crop.crop_bbox_xyxy
        or observation.crop_width != crop.width or observation.crop_height != crop.height
        or (observation.config_sha256 if isinstance(attempt, VisionAttemptV1) else observation.producer.config_sha256) != attempt.config_sha256
    ):
        raise InputError('OCR observation does not match its actual current crop, input and configuration')
    if isinstance(attempt, OCRAttemptV1) and observation is not None and (
        attempt.executable_sha256 != observation.producer.executable_sha256
        or attempt.engine_version != observation.producer.engine_version
        or attempt.language_data != observation.producer.language_data
    ):
        raise InputError('OCR producer identity differs from the actual attempt')
    return attempt


def apply_supplemental_review(audit: AuditInputV1, report: AuditReportV3,
                            review: SupplementalReviewV1, *, renderer_source: BoundRenderEvidence | None = None) -> AuditReportV3:
    audit = _validated_input(audit)
    _validate_image_bytes(audit)
    report = _bound_report(report, audit, model=report.model, live=report.execution_mode == 'live')
    try:
        review = SupplementalReviewV1.model_validate_json(review.model_dump_json())
    except (ValueError, TypeError, AttributeError):
        raise InputError('invalid supplementary review contract') from None
    if (review.sample_id != audit.sample_id or review.image_sha256 != audit.image.sha256
            or review.input_fingerprint != input_fingerprint(audit)):
        raise InputError('supplementary evidence does not match the complete audit')
    known = {x.copy_id for x in audit.approved_copy or ()}
    copy_ids = set(known)
    if audit.layout_context:
        known.update(x.element_id for x in audit.layout_context.elements)
    if any(x.element_id is not None and x.element_id not in known for x in review.findings):
        raise InputError('supplementary finding refers to an unknown element')
    subject_ids = known | {x.keyword_id for x in audit.keywords} | {x.constraint_id for x in audit.user_constraints}
    if any(x.subject_id is not None and x.subject_id not in subject_ids for x in review.gaps):
        raise InputError('supplementary gap refers to an unknown subject')
    assets = {x.asset_key for x in audit.asset_context.assets} if audit.asset_context else set()
    if any((x.element_id is not None and x.element_id not in known)
           or (x.asset_key is not None and x.asset_key not in assets) for x in review.proposed_hints):
        raise InputError('supplementary hint refers to an unknown element or asset')
    if review.renderer is not None and review.renderer.authority is not None:
        binding = review.renderer_binding
        if binding is None or (binding.input_fingerprint != input_fingerprint(audit)
            or binding.image_sha256 != audit.image.sha256 or binding.evidence_id != review.renderer.evidence_id
            or binding.revision != review.renderer.evidence_revision or binding.authority != review.renderer.authority):
            raise InputError('renderer analysis is missing its current evidence binding')
        if not isinstance(renderer_source, BoundRenderEvidence):
            raise InputError('renderer facts require an explicit current-source resolver, not saved JSON alone')
        current = validate_render_evidence(audit, renderer_source)
        fresh = analyze_render_evidence(audit, current)
        if (fresh != review.renderer or review.findings != fresh.findings
                or review.proposed_hints != fresh.repair_hints or binding.source_sha256 != current.reference.sha256):
            raise InputError('renderer analysis/facts changed or differ from their registered source')
    elif review.renderer_binding is not None:
        raise InputError('renderer binding has no matching analysis')
    elif review.findings or review.proposed_hints:
        raise InputError('confirmed supplementary facts/hints require their explicit evidence source')
    if len({x.copy_id for x in review.ocr}) != len(review.ocr):
        raise InputError('supplementary OCR records must identify distinct copy items')
    for record in review.ocr:
        if record.copy_id not in copy_ids:
            raise InputError('supplementary OCR record refers to unknown copy')
        crop = crop_evidence(audit, record.region_bbox_xyxy)
        _checked_attempt(audit, crop, record.attempt, backend=record.backend)
    issues = (*report.issues, *review.findings)
    gaps = tuple(x for x in review.gaps if x.required_for_release)
    coverage = tuple(
        x.model_copy(update={'status': 'unknown', 'state': 'unknown', 'reason': 'Selected supplementary evidence is missing or conflicting.'})
        if x.status != 'fail' and any(g.check_id == x.check_id for g in gaps) else x
        for x in report.coverage
    )
    reasons = tuple(
        f'{g.code} [{g.check_id}; object={g.subject_id or "unlocated"}]: {g.doubt} '
        f'Missing evidence: {g.missing_evidence} Next step for A1: {g.suggestion}' for g in gaps
    )
    uncertainties = (*report.uncertainties, *reasons)
    failed = any(x.severity in ('blocking', 'major') for x in issues) or any(
        x.required and x.status == 'fail' for x in coverage
    ) or any(x.status == 'fail' for x in report.keyword_checks)
    incomplete = bool(uncertainties) or any(x.required and x.status == 'unknown' for x in coverage)
    decision = 'fail' if failed else 'unknown' if incomplete else 'pass'
    safe_hint = decision == 'fail' and not incomplete and not any(x.state == 'unknown' for x in coverage)
    hint = report.repair_hint if safe_hint else None
    if hint is None and safe_hint and review.proposed_hints:
        hint = review.proposed_hints[0]
    result = report.model_dump(mode='python')
    result.update(issues=issues, coverage=coverage, uncertainties=uncertainties, decision=decision, repair_hint=hint)
    return _bound_report(AuditReportV3.model_validate(result), audit, model=report.model,
                         live=report.execution_mode == 'live')


class EvidenceReviewer:
    """A4 caller explicitly selects evidence and policy. Default engine has none."""
    def __init__(self, *, renderer: BoundRenderEvidence | None = None, require_renderer: bool = False,
                 ocr: TesseractOCR | VisionOCR | None = None, regions: tuple[OCRRegionV1, ...] = (),
                 max_tool_attempts: int = 2, tool_timeout_seconds: float = 30.0):
        if type(require_renderer) is not bool:
            raise InputError('renderer gate must be explicit bool')
        self.renderer, self.require_renderer, self.ocr = renderer, require_renderer, ocr
        self.regions = tuple(OCRRegionV1.model_validate(x.model_dump(mode='python')) for x in regions)
        if len({x.copy_id for x in self.regions}) != len(self.regions):
            raise InputError('OCR regions must identify each copy at most once')
        if self.regions and ocr is None:
            raise InputError('selected OCR regions require an explicitly selected backend')
        if ocr is not None and not isinstance(ocr, (TesseractOCR, VisionOCR)):
            # Framework imports remain optional for standalone core users.
            try:
                from .adapters.framework_evidence import FrameworkOCRBackend
            except ImportError:
                raise InputError('framework OCR requires the explicitly installed framework adapter dependencies') from None
            if not isinstance(ocr, FrameworkOCRBackend):
                raise InputError('select a supported explicit OCR backend')
        self.backend_family = ('apple_vision' if isinstance(ocr, VisionOCR) else
                               'tesseract' if isinstance(ocr, TesseractOCR) or ocr is None else ocr.backend_family)
        # Validate budget before a primary model can be called.
        VisionBudget(max_attempts=max_tool_attempts, total_timeout_seconds=tool_timeout_seconds)
        self.max_tool_attempts, self.tool_timeout_seconds = max_tool_attempts, tool_timeout_seconds
        self._busy = False
        self.last_review: SupplementalReviewV1 | None = None
        self.last_tool_budget: OCRBudget | VisionBudget | None = None

    def preflight(self, audit: AuditInputV1) -> None:
        audit = _validated_input(audit)
        _validate_image_bytes(audit)
        copies = {x.copy_id for x in audit.approved_copy or ()}
        if any(region.copy_id not in copies for region in self.regions):
            raise InputError('OCR region must refer to current approved copy')
        for region in self.regions:
            crop_evidence(audit, region.bbox_xyxy)
        if self.renderer is not None:
            validate_render_evidence(audit, self.renderer)

    async def __call__(self, audit: AuditInputV1, report: AuditReportV3) -> SupplementalReviewV1:
        if self._busy:
            raise InputError('an evidence reviewer cannot be shared concurrently')
        self._busy = True
        self.last_review = None
        self.last_tool_budget = None
        try:
            self.preflight(audit)
            copies = {x.copy_id: x for x in audit.approved_copy or ()}
            if any(region.copy_id not in copies for region in self.regions):
                raise InputError('OCR region must refer to current approved copy')
            renderer = analyze_render_evidence(audit, self.renderer) if self.renderer or self.require_renderer else None
            findings, hints = (() if renderer is None else renderer.findings), (() if renderer is None else renderer.repair_hints)
            gaps: list[EvidenceGapV1] = []
            mapping = {'source_binding': 'layout_geometry', 'render_geometry': 'layout_geometry',
                       'render_text': 'text_accuracy', 'glyph_coverage': 'text_readability', 'overlap_policy': 'layout_quality'}
            if renderer is not None and self.require_renderer:
                for check in renderer.checks:
                    if check.status == 'unknown' and (copies or check.check_id not in ('render_text', 'glyph_coverage')):
                        gaps.append(EvidenceGapV1(check_id=mapping[check.check_id], subject_id=None,
                            code='render_evidence_incomplete', required_for_release=True, doubt=check.reason,
                            missing_evidence='Current registered renderer facts and their authoritative version binding.',
                            suggestion='Provide actual ink/font/drawing facts for the current candidate; preserve original requirements.'))
            records = []
            budget = (VisionBudget(max_attempts=self.max_tool_attempts, total_timeout_seconds=self.tool_timeout_seconds)
                      if self.backend_family == 'apple_vision' else OCRBudget(max_attempts=self.max_tool_attempts,
                                                                                total_timeout_seconds=self.tool_timeout_seconds))
            self.last_tool_budget = budget
            for region in self.regions:
                crop = crop_evidence(audit, region.bbox_xyxy)
                attempt = await self.ocr.observe(audit, crop, budget)
                backend = 'apple_vision' if isinstance(attempt, VisionAttemptV1) else 'tesseract'
                attempt = _checked_attempt(audit, crop, attempt,
                    backend=self.backend_family,
                    config_sha256=self.ocr.config_sha256)
                records.append(OCRReviewRecordV1(copy_id=region.copy_id, region_bbox_xyxy=region.bbox_xyxy,
                                                 backend=backend, attempt=attempt))
                if attempt.status != 'observed':
                    doubt, missing, code = f'OCR did not complete: {attempt.error_code}.', 'A bounded actual OCR observation.', 'ocr_unavailable'
                    action = (getattr(attempt, 'suggested_action', None) or
                              'Verify the explicitly selected helper, language support and bounded tool deadline; retain the candidate and do not retry or regenerate blindly.')
                else:
                    observation = attempt.observation
                    if isinstance(attempt, VisionAttemptV1):
                        parts = sorted(observation.lines, key=lambda x: (x.crop_bbox_xyxy[1], x.crop_bbox_xyxy[0]))
                        text = '\n'.join(x.text for x in parts)
                        reliable = bool(parts) and all(x.confidence >= region.minimum_confidence for x in parts)
                    else:
                        parts = sorted(observation.words, key=lambda x: (x.block_num, x.par_num, x.line_num, x.word_num))
                        rows: dict[tuple[int, int, int], list[str]] = {}
                        for word in parts:
                            rows.setdefault((word.block_num, word.par_num, word.line_num), []).append(word.text)
                        text = '\n'.join(' '.join(words) for words in rows.values())
                        reliable = bool(parts) and all(x.text and x.confidence is not None and x.confidence / 100 >= region.minimum_confidence for x in parts)
                    if reliable and text_matches(text, copies[region.copy_id]):
                        continue  # corroboration never promotes an unknown primary check
                    doubt = 'OCR text is missing, uncertain or differs from the approved wording; this is not a confirmed image defect.'
                    missing, code = 'Consistent pixel transcription for this explicitly selected text region.', 'ocr_text_uncertain'
                    action = 'Have A1 verify this region and current text evidence before changing exact copy; do not regenerate blindly.'
                gaps.append(EvidenceGapV1(check_id='text_accuracy', subject_id=region.copy_id, code=code,
                    required_for_release=region.required_for_release, doubt=doubt, missing_evidence=missing,
                    suggestion=action))
            render_binding = None
            if self.renderer is not None:
                checked = validate_render_evidence(audit, self.renderer)
                render_binding = RenderReviewBindingV1(evidence_id=checked.reference.evidence_id,
                    revision=checked.reference.revision, authority=checked.authority,
                    image_sha256=audit.image.sha256, input_fingerprint=input_fingerprint(audit), source_sha256=checked.reference.sha256)
            self.last_review = SupplementalReviewV1(sample_id=audit.sample_id, image_sha256=audit.image.sha256,
                input_fingerprint=input_fingerprint(audit), renderer=renderer, ocr=tuple(records),
                renderer_binding=render_binding, findings=findings, gaps=tuple(gaps), proposed_hints=hints)
            return self.last_review
        finally:
            self._busy = False

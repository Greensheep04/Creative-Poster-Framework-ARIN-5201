"""Deterministic layout checks.

These functions only state facts that can be measured: boxes, real text ink,
declared overlaps and planned keyword bindings.  They never claim that a poster
looks good; semantic and aesthetic judgement belongs to the quality critic.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .layout_fonts import FontRegistry, default_font_registry, measure_text_block
from .layout_spec import (
    ImageElement,
    PosterSpec,
    TextElement,
    element_box,
    parse_poster_spec,
    rect_intersection,
)
from .layout_templates import MIN_BODY_FONT_SIZE_PX
from .types import CANVAS_HEIGHT, CANVAS_WIDTH, OriginalRequest


def _issue(
    code: str,
    *,
    severity: str,
    message: str,
    element_ids: tuple[str, ...] = (),
    evidence: Mapping[str, object] | None = None,
    blocking: bool = True,
) -> dict[str, object]:
    return {
        "code": code,
        "severity": severity,
        "blocking": blocking,
        "element_ids": list(element_ids),
        "message": message,
        "evidence": dict(evidence or {}),
    }


def _schema_failure(error: object) -> dict[str, object]:
    return {
        "ok": False,
        "issues": [
            _issue(
                "SCHEMA_INVALID",
                severity="blocker",
                message=str(error),
            )
        ],
        "measurements": [],
    }


def check_layout_geometry(
    spec: Mapping[str, Any] | PosterSpec,
    *,
    registry: FontRegistry | None = None,
) -> dict[str, object]:
    """Bounds, real text fitting, safe margins and deliberate-overlap checks."""

    try:
        parsed = spec if isinstance(spec, PosterSpec) else parse_poster_spec(dict(spec))
    except Exception as error:
        return _schema_failure(error)
    active_registry = registry or default_font_registry()
    issues: list[dict[str, object]] = []
    measurements: list[dict[str, object]] = []
    for element in parsed.elements:
        if not isinstance(element, TextElement):
            continue
        measurement = measure_text_block(
            element.text,
            font_family_key=element.font_family_key,
            font_weight=element.font_weight,
            font_size_px=element.font_size_px,
            line_height_px=element.line_height_px,
            max_width_px=element.box.width,
            max_lines=element.max_lines,
            box_height_px=element.box.height,
            registry=active_registry,
        )
        record = measurement.as_json()
        record["element_id"] = element.id
        measurements.append(record)
        evidence = {
            "lines": list(measurement.lines),
            "widest_line_px": round(measurement.widest_line_px, 2),
            "available_width_px": element.box.width,
            "required_height_px": measurement.required_height_px,
            "available_height_px": element.box.height,
            "font_size_px": element.font_size_px,
        }
        if measurement.missing_glyphs:
            issues.append(
                _issue(
                    "MISSING_GLYPH",
                    severity="blocker",
                    message=(
                        f"font {element.font_family_key!r} cannot render "
                        f"{''.join(measurement.missing_glyphs)!r}"
                    ),
                    element_ids=(element.id,),
                    evidence={"missing_glyphs": list(measurement.missing_glyphs)},
                )
            )
        if not measurement.fits_width or not measurement.fits_lines:
            issues.append(
                _issue(
                    "TEXT_OVERFLOW",
                    severity="blocker",
                    message=f"copy of {element.id!r} does not fit its box width or line budget",
                    element_ids=(element.id,),
                    evidence=evidence,
                )
            )
        elif not measurement.fits_height:
            issues.append(
                _issue(
                    "TEXT_HEIGHT_OVERFLOW",
                    severity="blocker",
                    message=f"copy of {element.id!r} needs more height than its box",
                    element_ids=(element.id,),
                    evidence=evidence,
                )
            )
        if element.font_size_px < MIN_BODY_FONT_SIZE_PX:
            issues.append(
                _issue(
                    "TEXT_TOO_SMALL",
                    severity="major",
                    message=(
                        f"{element.id!r} uses {element.font_size_px}px, below the "
                        f"{MIN_BODY_FONT_SIZE_PX}px readability floor"
                    ),
                    element_ids=(element.id,),
                    evidence={"font_size_px": element.font_size_px},
                    blocking=False,
                )
            )
    margin = parsed.design_tokens.safe_margin_px
    for element in parsed.elements:
        if not isinstance(element, TextElement):
            continue  # images may bleed by template design
        x, y, width, height = element_box(element)
        if (
            x < margin
            or y < margin
            or x + width > CANVAS_WIDTH - margin
            or y + height > CANVAS_HEIGHT - margin
        ):
            issues.append(
                _issue(
                    "SAFE_MARGIN_VIOLATION",
                    severity="major",
                    message=f"{element.id!r} leaves the {margin}px safe margin",
                    element_ids=(element.id,),
                    evidence={"box": element.box.model_dump(mode="json")},
                )
            )
    declared = {frozenset(pair) for pair in parsed.allowed_overlaps}
    elements = list(parsed.elements)
    for first_index, first in enumerate(elements):
        for second in elements[first_index + 1 :]:
            overlap = rect_intersection(element_box(first), element_box(second))
            if overlap == 0:
                continue
            if frozenset((first.id, second.id)) in declared:
                continue
            both_text = isinstance(first, TextElement) and isinstance(second, TextElement)
            issues.append(
                _issue(
                    "UNEXPECTED_OVERLAP",
                    severity="blocker" if both_text else "major",
                    message=(
                        f"{first.id!r} and {second.id!r} overlap without being "
                        "declared in allowed_overlaps"
                    ),
                    element_ids=(first.id, second.id),
                    evidence={"overlap_px2": overlap},
                )
            )
    blocking = any(bool(issue["blocking"]) for issue in issues)
    return {
        "ok": not blocking,
        "issues": issues,
        "measurements": measurements,
        "summary": {
            "elements": len(parsed.elements),
            "text_elements": len(measurements),
            "declared_overlaps": len(declared),
            "issues": len(issues),
        },
    }


def check_planned_keyword_coverage(
    request: OriginalRequest,
    spec: Mapping[str, Any] | PosterSpec,
) -> dict[str, object]:
    """Confirm that every original keyword is planned onto known elements.

    A ``planned`` status only means the layout assigned the keyword a visible
    element or layout property.  Whether the rendered pixels actually express it
    is a separate, image-level judgement.
    """

    try:
        parsed = spec if isinstance(spec, PosterSpec) else parse_poster_spec(dict(spec))
    except Exception as error:
        return _schema_failure(error)
    known_elements = {element.id for element in parsed.elements}
    by_id = {binding.keyword_id: binding for binding in parsed.keyword_element_bindings}
    request_ids = {keyword.keyword_id for keyword in request.keywords}
    keywords: list[dict[str, object]] = []
    for keyword in request.keywords:
        binding = by_id.get(keyword.keyword_id)
        if binding is None:
            keywords.append(
                {
                    "keyword_id": keyword.keyword_id,
                    "text": keyword.text,
                    "status": "missing",
                    "element_ids": [],
                    "layout_evidence": None,
                }
            )
            continue
        stray = [item for item in binding.element_ids if item not in known_elements]
        evidence = (binding.layout_evidence or "").strip()
        if stray:
            status = "missing"
        elif not evidence:
            status = "unverified"
        else:
            status = "planned"
        keywords.append(
            {
                "keyword_id": keyword.keyword_id,
                "text": keyword.text,
                "status": status,
                "element_ids": list(binding.element_ids),
                "layout_evidence": binding.layout_evidence,
            }
        )
    unknown = sorted(set(by_id) - request_ids)
    ok = all(item["status"] == "planned" for item in keywords) and not unknown
    return {
        "ok": ok,
        "keywords": keywords,
        "unknown_keyword_ids": unknown,
        "planned": sum(1 for item in keywords if item["status"] == "planned"),
        "total": len(keywords),
    }


def check_image_placements(spec: Mapping[str, Any] | PosterSpec) -> dict[str, object]:
    """Asset references and image boxes, without judging image content."""

    try:
        parsed = spec if isinstance(spec, PosterSpec) else parse_poster_spec(dict(spec))
    except Exception as error:
        return _schema_failure(error)
    asset_ids = {asset.asset_id: asset for asset in parsed.asset_plan}
    issues: list[dict[str, object]] = []
    for element in parsed.elements:
        if not isinstance(element, ImageElement):
            continue
        asset = asset_ids.get(element.asset_id)
        if asset is None:
            issues.append(
                _issue(
                    "MISSING_ASSET",
                    severity="blocker",
                    message=f"{element.id!r} references asset {element.asset_id!r} that is not planned",
                    element_ids=(element.id,),
                )
            )
            continue
        box = element.box
        target = asset.target_size
        if (target.width, target.height) != (box.width, box.height):
            issues.append(
                _issue(
                    "ASSET_TARGET_MISMATCH",
                    severity="major",
                    message=(
                        f"asset {asset.asset_id!r} targets "
                        f"{target.width}x{target.height} but is placed at "
                        f"{box.width}x{box.height}; the producer must record the crop"
                    ),
                    element_ids=(element.id,),
                    evidence={
                        "target_size": target.model_dump(mode="json"),
                        "box": box.model_dump(mode="json"),
                    },
                    blocking=False,
                )
            )
    return {"ok": not any(issue["blocking"] for issue in issues), "issues": issues}

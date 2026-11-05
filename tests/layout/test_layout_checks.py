from __future__ import annotations

import copy

import pytest

from layout_support import FONTS_AVAILABLE, make_brief, make_request
from poster_framework import (
    check_image_placements,
    check_layout_geometry,
    check_planned_keyword_coverage,
    plan_poster_spec,
)

requires_fonts = pytest.mark.skipif(
    not FONTS_AVAILABLE, reason="no CJK-capable font installed"
)


def _spec(**brief_overrides: object) -> dict[str, object]:
    return plan_poster_spec(make_brief(**brief_overrides), request=make_request())


def _codes(result: dict[str, object]) -> list[str]:
    return [issue["code"] for issue in result["issues"]]


def _text_element(spec: dict[str, object], element_id: str) -> dict[str, object]:
    return next(
        element
        for element in spec["elements"]
        if element["id"] == element_id
    )


@requires_fonts
def test_clean_plan_passes_geometry_with_measurements() -> None:
    spec = _spec()
    result = check_layout_geometry(spec)
    assert result["ok"] is True
    assert result["issues"] == []
    assert {item["element_id"] for item in result["measurements"]} == {"title", "subtitle"}
    assert all(item["fits"] for item in result["measurements"])


@requires_fonts
def test_real_overflow_is_reported_with_measured_evidence() -> None:
    spec = copy.deepcopy(_spec())
    title = _text_element(spec, "title")
    title["box"]["width"] = 60
    result = check_layout_geometry(spec)
    assert result["ok"] is False
    issue = next(item for item in result["issues"] if item["code"] == "TEXT_OVERFLOW")
    assert issue["blocking"] is True
    assert issue["element_ids"] == ["title"]
    assert issue["evidence"]["widest_line_px"] > 60


@requires_fonts
def test_safe_margin_violation_blocks_release() -> None:
    spec = copy.deepcopy(_spec())
    _text_element(spec, "title")["box"]["x"] = 10
    result = check_layout_geometry(spec)
    assert result["ok"] is False
    assert "SAFE_MARGIN_VIOLATION" in _codes(result)


@requires_fonts
def test_undeclared_text_over_image_overlap_is_reported() -> None:
    spec = copy.deepcopy(_spec())
    subtitle = _text_element(spec, "subtitle")
    subtitle["box"]["y"] = 1000
    result = check_layout_geometry(spec)
    assert result["ok"] is False
    assert "UNEXPECTED_OVERLAP" in _codes(result)


@requires_fonts
def test_declared_overlap_is_not_reported() -> None:
    spec = plan_poster_spec(
        make_brief(template_id="full_bleed_v1"),
        request=make_request(),
    )
    assert ["hero", "title"] in spec["allowed_overlaps"]
    result = check_layout_geometry(spec)
    assert result["ok"] is True
    assert "UNEXPECTED_OVERLAP" not in _codes(result)


@requires_fonts
def test_small_copy_is_a_warning_not_a_blocker() -> None:
    spec = copy.deepcopy(_spec())
    title = _text_element(spec, "title")
    title["font_size_px"] = 20
    title["line_height_px"] = 24
    result = check_layout_geometry(spec)
    small = next(item for item in result["issues"] if item["code"] == "TEXT_TOO_SMALL")
    assert small["blocking"] is False
    assert result["ok"] is True


@requires_fonts
def test_keyword_coverage_reports_planned_missing_and_unknown() -> None:
    request = make_request()
    spec = _spec()
    covered = check_planned_keyword_coverage(request, spec)
    assert covered["ok"] is True
    assert covered["planned"] == covered["total"] == 3
    assert all(item["element_ids"] for item in covered["keywords"])

    dropped = copy.deepcopy(spec)
    dropped["keyword_element_bindings"].pop()
    partial = check_planned_keyword_coverage(request, dropped)
    assert partial["ok"] is False
    assert any(item["status"] == "missing" for item in partial["keywords"])

    no_evidence = copy.deepcopy(spec)
    for binding in no_evidence["keyword_element_bindings"]:
        binding["layout_evidence"] = None
    unknown = check_planned_keyword_coverage(request, no_evidence)
    assert unknown["ok"] is False
    assert {item["status"] for item in unknown["keywords"]} == {"unverified"}


@requires_fonts
def test_keyword_coverage_flags_ids_outside_the_request() -> None:
    request = make_request()
    spec = copy.deepcopy(_spec())
    spec["keyword_element_bindings"].append(
        {"keyword_id": "kw-not-in-request", "element_ids": ["hero"], "layout_evidence": "x"}
    )
    result = check_planned_keyword_coverage(request, spec)
    assert result["ok"] is False
    assert result["unknown_keyword_ids"] == ["kw-not-in-request"]


@requires_fonts
def test_image_target_mismatch_is_reported_as_non_blocking() -> None:
    spec = copy.deepcopy(_spec())
    spec["asset_plan"][0]["target_size"]["width"] = 600
    result = check_image_placements(spec)
    assert result["ok"] is True
    mismatch = next(
        issue for issue in result["issues"] if issue["code"] == "ASSET_TARGET_MISMATCH"
    )
    assert mismatch["blocking"] is False

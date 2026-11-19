from __future__ import annotations

import asyncio

import pytest

from layout_support import (
    FONTS_AVAILABLE,
    StubContext,
    make_brief,
    make_inputs,
    make_request,
    memory_artifact,
    target_for,
)
from poster_framework import (
    BRIEF_FORMAT,
    LayoutContractError,
    LayoutPlannerAgent,
    LEGACY_BRIEF_FORMAT,
    POSTER_SPEC_FORMAT,
    RevisionAction,
    TextOverflowError,
    VersionDependency,
    layout_planner_registration,
    plan_poster_spec,
    validate_poster_spec,
)
from poster_framework.layout_planner import LAYOUT_BRIEF_FORMAT

requires_fonts = pytest.mark.skipif(
    not FONTS_AVAILABLE, reason="no CJK-capable font installed"
)


def _run(brief, *, request=None, context=None, agent=None):
    request = request or make_request()
    context = context or StubContext(request)
    inputs = make_inputs(brief, request)
    return asyncio.run((agent or LayoutPlannerAgent()).run(inputs, context))


@requires_fonts
def test_agent_returns_the_shared_spec_format() -> None:
    output = _run(make_brief())
    assert output.content_format == POSTER_SPEC_FORMAT
    assert output.review is None
    validate_poster_spec(output.content)
    assert output.content["run_id"] == "layout-test-run"
    assert output.content["based_on"] == {"brief_version": 1}


@requires_fonts
def test_agent_accepts_the_legacy_brief_version() -> None:
    request = make_request()
    inputs = make_inputs(make_brief(), request)
    legacy = inputs.model_copy(update={"content_format": LEGACY_BRIEF_FORMAT})
    output = asyncio.run(LayoutPlannerAgent().run(legacy, StubContext(request)))
    validate_poster_spec(output.content)


def test_registration_declares_input_and_output_formats() -> None:
    registration = layout_planner_registration()
    assert registration.role.value == "layout_planner"
    assert POSTER_SPEC_FORMAT in registration.output_formats
    assert BRIEF_FORMAT in registration.input_formats
    assert LAYOUT_BRIEF_FORMAT in registration.input_formats


@requires_fonts
def test_long_title_shrinks_instead_of_overflowing() -> None:
    short = _run(make_brief(copy={"title": "静海"}))
    long = _run(make_brief(copy={"title": "静海之蓝与漫长的宁静午后"}))
    short_size = next(e for e in short.content["elements"] if e["id"] == "title")["font_size_px"]
    long_size = next(e for e in long.content["elements"] if e["id"] == "title")["font_size_px"]
    assert long_size < short_size
    assert long_size >= 44


@requires_fonts
def test_unfittable_title_fails_the_stage() -> None:
    with pytest.raises(TextOverflowError):
        _run(make_brief(copy={"title": "静" * 200}))


@requires_fonts
def test_brief_cannot_rewrite_the_original_keywords() -> None:
    with pytest.raises(LayoutContractError):
        _run(make_brief(keywords=["海洋", "喧闹"]))


@requires_fonts
def test_missing_copy_is_rejected() -> None:
    with pytest.raises(LayoutContractError):
        _run({"copy": {"subtitle": "只有副标题"}})


@requires_fonts
def test_unknown_template_is_a_contract_error() -> None:
    with pytest.raises(LayoutContractError):
        _run(make_brief(template_id="not_a_template"))


@requires_fonts
def test_brief_can_pin_a_template_and_declare_overlaps() -> None:
    output = _run(make_brief(template_id="full_bleed_v1"))
    content = output.content
    assert content["template_id"] == "full_bleed_v1"
    assert ["hero", "title"] in content["allowed_overlaps"]
    hero = next(e for e in content["elements"] if e["id"] == "hero")
    assert hero["box"] == {"x": 0, "y": 0, "width": 720, "height": 1280}
    assert hero["focus_point_norm"] == [0.5, 0.45]


@requires_fonts
def test_agent_configuration_can_pin_a_template() -> None:
    agent = LayoutPlannerAgent(template_id="split_stack_v1")
    output = _run(make_brief(), agent=agent)
    assert output.content["template_id"] == "split_stack_v1"
    title = next(e for e in output.content["elements"] if e["id"] == "title")
    assert title["box"]["y"] == 808


@requires_fonts
def test_palette_and_text_color_follow_the_brief() -> None:
    brief = make_brief(palette={"colors": ["deep ocean blue (#0B2A4C)", "#A0D8E8"]})
    content = _run(brief).content
    assert content["design_tokens"]["palette"][:2] == ["#0B2A4C", "#A0D8E8"]
    title = next(e for e in content["elements"] if e["id"] == "title")
    assert title["color"] == "#0B2A4C"


@requires_fonts
def test_english_subject_produces_an_english_asset_prompt() -> None:
    brief = make_brief(
        visual_direction={"main_subject": "a calm ocean at dawn", "style": "minimal photography"}
    )
    prompt = _run(brief).content["asset_plan"][0]["prompt"]
    assert prompt.startswith("a calm ocean at dawn.")
    assert "Style: minimal photography." in prompt
    assert "no text" in prompt


@requires_fonts
def test_bindings_use_brief_interpretation_evidence() -> None:
    request = make_request()
    spec = plan_poster_spec(make_brief(), request=request)
    bindings = {item["keyword_id"]: item for item in spec["keyword_element_bindings"]}
    first = request.keywords[0].keyword_id
    brief = make_brief(
        keyword_bindings=[
            {
                "keyword_id": first,
                "interpretation": "可识别的海面与海平线",
                "visual_evidence": ["海面", "海平线"],
            }
        ]
    )
    rebuilt = plan_poster_spec(brief, request=request)
    evidence = next(
        item["layout_evidence"]
        for item in rebuilt["keyword_element_bindings"]
        if item["keyword_id"] == first
    )
    assert "海面" in evidence and "海平线" in evidence
    assert bindings[first]["element_ids"] == ["hero"]


@requires_fonts
def test_revise_layout_rotates_template_and_preserves_copy() -> None:
    request = make_request()
    first = plan_poster_spec(make_brief(), request=request)
    context = StubContext(
        request,
        action=RevisionAction.revise_layout,
        target=VersionDependency(artifact_id="artifact-layout", revision="rev-1"),
        artifacts=[memory_artifact(first)],
        invocation_id="layout-invocation-2",
    )
    output = _run(make_brief(), request=request, context=context)
    revised = output.content
    assert revised["spec_version"] == 2
    assert revised["template_id"] == "full_bleed_v1"
    assert revised["template_id"] != first["template_id"]
    original_title = next(e for e in first["elements"] if e["id"] == "title")["text"]
    revised_title = next(e for e in revised["elements"] if e["id"] == "title")["text"]
    assert revised_title == original_title


@requires_fonts
def test_revise_layout_applies_allowlisted_patch() -> None:
    request = make_request()
    first = plan_poster_spec(make_brief(), request=request)
    patch = {
        "changes": [
            {"target_type": "element", "target_id": "title", "field": "box.y",
             "old_value": 64, "new_value": 84},
        ],
        "preserve_element_ids": ["hero", "subtitle"],
        "preserve_copy": True,
    }
    context = StubContext(
        request,
        action=RevisionAction.revise_layout,
        target=VersionDependency(artifact_id="artifact-layout", revision="rev-1"),
        artifacts=[memory_artifact(first)],
    )
    revised = _run(make_brief(layout_patch=patch), request=request, context=context).content
    assert revised["spec_version"] == 2
    assert revised["template_id"] == first["template_id"]
    title = next(e for e in revised["elements"] if e["id"] == "title")
    assert title["box"]["y"] == 84
    assert title["text"] == next(e for e in first["elements"] if e["id"] == "title")["text"]


@requires_fonts
def test_stale_layout_patch_is_rejected() -> None:
    request = make_request()
    first = plan_poster_spec(make_brief(), request=request)
    patch = {
        "changes": [
            {"target_type": "element", "target_id": "title", "field": "box.y",
             "old_value": 10, "new_value": 84},
        ],
        "preserve_copy": True,
    }
    context = StubContext(
        request,
        action=RevisionAction.revise_layout,
        target=VersionDependency(artifact_id="artifact-layout", revision="rev-1"),
        artifacts=[memory_artifact(first)],
    )
    with pytest.raises(LayoutContractError):
        _run(make_brief(layout_patch=patch), request=request, context=context)


@requires_fonts
def test_patch_cannot_change_published_copy() -> None:
    request = make_request()
    first = plan_poster_spec(make_brief(), request=request)
    patch = {
        "changes": [
            {"target_type": "element", "target_id": "title", "field": "text",
             "new_value": "另一个标题"},
        ],
        "preserve_copy": True,
    }
    context = StubContext(
        request,
        action=RevisionAction.revise_layout,
        target=VersionDependency(artifact_id="artifact-layout", revision="rev-1"),
        artifacts=[memory_artifact(first)],
    )
    with pytest.raises(LayoutContractError):
        _run(make_brief(layout_patch=patch), request=request, context=context)


@requires_fonts
def test_patch_cannot_touch_preserved_elements() -> None:
    request = make_request()
    first = plan_poster_spec(make_brief(), request=request)
    patch = {
        "changes": [
            {"target_type": "element", "target_id": "hero", "field": "box.y",
             "new_value": 300},
        ],
        "preserve_element_ids": ["hero", "subtitle"],
        "preserve_copy": True,
    }
    context = StubContext(
        request,
        action=RevisionAction.revise_layout,
        target=VersionDependency(artifact_id="artifact-layout", revision="rev-1"),
        artifacts=[memory_artifact(first)],
    )
    with pytest.raises(LayoutContractError):
        _run(make_brief(layout_patch=patch), request=request, context=context)


@requires_fonts
def test_pinned_template_revision_requires_a_patch() -> None:
    request = make_request()
    first = plan_poster_spec(make_brief(), request=request)
    context = StubContext(
        request,
        action=RevisionAction.revise_layout,
        target=VersionDependency(artifact_id="artifact-layout", revision="rev-1"),
        artifacts=[memory_artifact(first)],
    )
    with pytest.raises(LayoutContractError):
        _run(make_brief(), request=request, context=context, agent=LayoutPlannerAgent(template_id="framed_hero_v1"))


@requires_fonts
def test_font_family_is_configurable() -> None:
    from poster_framework import MissingFontError, default_font_registry

    if not default_font_registry().weights("latin_sans"):
        pytest.skip("no Latin-only family installed")
    spec = plan_poster_spec(
        make_brief(copy={"title": "CALM SEA", "subtitle": "quiet blue"}),
        request=make_request(),
        font_family_key="latin_sans",
    )
    assert spec["design_tokens"]["font_family_key"] == "latin_sans"
    assert {
        element["font_family_key"]
        for element in spec["elements"]
        if element["type"] == "text"
    } == {"latin_sans"}

    with pytest.raises(MissingFontError):
        plan_poster_spec(
            make_brief(), request=make_request(), font_family_key="no_such_family"
        )


@requires_fonts
def test_planned_spec_has_no_leftover_mutable_state() -> None:
    first = _run(make_brief()).content
    second = _run(make_brief()).content
    assert first == second
    first["elements"][0]["box"]["x"] = 999
    assert second["elements"][0]["box"]["x"] == 36


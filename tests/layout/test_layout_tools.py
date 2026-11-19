from __future__ import annotations

import pytest

from layout_support import FONTS_AVAILABLE, StubContext, make_brief, make_request
from poster_framework import (
    AgentRole,
    ToolRegistry,
    check_layout_geometry,
    plan_poster_spec,
    register_layout_tools,
)

requires_fonts = pytest.mark.skipif(
    not FONTS_AVAILABLE, reason="no CJK-capable font installed"
)

EXPECTED_TOOLS = {
    "layout.check_geometry",
    "layout.check_keyword_coverage",
    "layout.get_font_registry",
    "layout.list_templates",
    "layout.measure_text_block",
    "layout.validate_spec",
}


def _registry() -> ToolRegistry:
    return register_layout_tools(ToolRegistry())


def test_layout_tools_are_registered_for_the_planner() -> None:
    registry = _registry()
    planner_tools = {tool.name for tool in registry.available_tools(AgentRole.layout_planner)}
    assert planner_tools == EXPECTED_TOOLS
    for tool in registry.available_tools(AgentRole.layout_planner):
        assert tool.description.strip()
        assert tool.parameters["type"] == "object"


def test_shared_measurement_tools_are_available_to_the_producer() -> None:
    registry = _registry()
    producer_tools = {
        tool.name for tool in registry.available_tools(AgentRole.visual_producer)
    }
    assert producer_tools == {
        "layout.get_font_registry",
        "layout.measure_text_block",
        "layout.validate_spec",
    }


@requires_fonts
def test_measure_tool_returns_measured_ink() -> None:
    definition = _registry().definition("layout.measure_text_block")
    arguments = definition.validate_arguments(
        {
            "text": "静海之蓝",
            "font_family_key": "noto_sans_sc",
            "font_weight": 700,
            "font_size_px": 72,
            "line_height_px": 94,
            "max_width_px": 648,
            "max_lines": 1,
            "box_height_px": 152,
        }
    )
    result = definition.invoke(arguments, None)
    assert result["fits"] is True
    assert result["lines"] == ["静海之蓝"]
    assert result["widest_line_px"] <= 648


def test_tool_arguments_are_typed() -> None:
    definition = _registry().definition("layout.measure_text_block")
    with pytest.raises(Exception):
        definition.validate_arguments(
            {
                "text": "静海",
                "font_family_key": "noto_sans_sc",
                "font_weight": 700,
                "font_size_px": "big",
                "line_height_px": 94,
                "max_width_px": 648,
                "max_lines": 1,
            }
        )


def test_template_and_font_tools_do_not_need_a_model() -> None:
    registry = _registry()
    templates = registry.definition("layout.list_templates").invoke(
        registry.definition("layout.list_templates").validate_arguments({}), None
    )
    assert [item["template_id"] for item in templates] == [
        "framed_hero_v1",
        "full_bleed_v1",
        "split_stack_v1",
    ]
    described = registry.definition("layout.get_font_registry").invoke(
        registry.definition("layout.get_font_registry").validate_arguments({}), None
    )
    assert "families" in described


@requires_fonts
def test_geometry_tool_matches_the_library_call() -> None:
    spec = plan_poster_spec(make_brief(), request=make_request())
    definition = _registry().definition("layout.check_geometry")
    arguments = definition.validate_arguments({"spec": spec})
    result = definition.invoke(arguments, None)
    assert result == check_layout_geometry(spec)
    assert result["ok"] is True


@requires_fonts
def test_coverage_tool_reads_the_run_request() -> None:
    request = make_request()
    spec = plan_poster_spec(make_brief(), request=request)
    definition = _registry().definition("layout.check_keyword_coverage")
    arguments = definition.validate_arguments({"spec": spec})
    result = definition.invoke(arguments, StubContext(request))
    assert result["ok"] is True
    assert result["total"] == len(request.keywords)


@requires_fonts
def test_validate_tool_reports_invalid_specs_without_raising() -> None:
    spec = plan_poster_spec(make_brief(), request=make_request())
    broken = dict(spec)
    broken["template_id"] = "unknown_template"
    definition = _registry().definition("layout.validate_spec")
    result = definition.invoke(definition.validate_arguments({"spec": broken}), None)
    assert result["ok"] is False
    assert "template" in str(result["error"]).lower()

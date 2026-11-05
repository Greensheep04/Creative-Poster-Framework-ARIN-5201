"""Layout tools exposed to agents through the framework ToolRegistry.

Agents decide *what* to ask; these tools decide *how* it is measured.  They are
plain functions with typed parameters, so the registry can derive JSON Schemas
and validate arguments before anything runs.
"""

from __future__ import annotations

from typing import Any

from .layout_checks import check_layout_geometry, check_planned_keyword_coverage
from .layout_fonts import default_font_registry, measure_text_block as _measure
from .layout_spec import validate_poster_spec as _validate
from .layout_templates import describe_templates
from .tools import ToolRegistry
from .types import AgentContext, AgentRole


def register_layout_tools(registry: ToolRegistry) -> ToolRegistry:
    """Register the A2 tool set on ``registry`` and return it."""

    registry.register(
        "layout.list_templates",
        list_layout_templates,
        description=(
            "List the controlled layout templates with their hero slot, copy slots, "
            "size limits and intentional overlaps."
        ),
        allowed_roles={AgentRole.layout_planner},
    )
    registry.register(
        "layout.get_font_registry",
        get_font_registry,
        description=(
            "Describe installed font families, real weights, file hashes and the "
            "search paths used, so no agent invents a font that does not exist."
        ),
        allowed_roles={AgentRole.layout_planner, AgentRole.visual_producer},
    )
    registry.register(
        "layout.measure_text_block",
        measure_text_block_tool,
        description=(
            "Measure real text ink with Pillow and the installed font: wraps lines, "
            "returns ink bounds and whether the block fits width, height and line budget."
        ),
        allowed_roles={AgentRole.layout_planner, AgentRole.visual_producer},
    )
    registry.register(
        "layout.check_geometry",
        check_layout_geometry_tool,
        description=(
            "Check a PosterSpec for out-of-canvas text, real copy overflow, missing "
            "glyphs, safe-margin violations and undeclared overlaps."
        ),
        allowed_roles={AgentRole.layout_planner},
    )
    registry.register(
        "layout.check_keyword_coverage",
        check_keyword_coverage_tool,
        description=(
            "Confirm that every original keyword is planned onto a known element and "
            "carries layout evidence. This proves planning, not rendered pixels."
        ),
        allowed_roles={AgentRole.layout_planner},
    )
    registry.register(
        "layout.validate_spec",
        validate_spec_tool,
        description="Validate a candidate PosterSpec against the shared layout contract.",
        allowed_roles={AgentRole.layout_planner, AgentRole.visual_producer},
    )
    return registry


def list_layout_templates() -> list[dict[str, object]]:
    return describe_templates()


def get_font_registry() -> dict[str, object]:
    return default_font_registry().describe()


def measure_text_block_tool(
    text: str,
    font_family_key: str,
    font_weight: int,
    font_size_px: int,
    line_height_px: int,
    max_width_px: int,
    max_lines: int,
    box_height_px: int | None = None,
) -> dict[str, object]:
    measurement = _measure(
        text,
        font_family_key=font_family_key,
        font_weight=font_weight,
        font_size_px=font_size_px,
        line_height_px=line_height_px,
        max_width_px=max_width_px,
        max_lines=max_lines,
        box_height_px=box_height_px,
    )
    return measurement.as_json()


def check_layout_geometry_tool(spec: dict[str, Any]) -> dict[str, object]:
    return check_layout_geometry(spec)


def check_keyword_coverage_tool(
    spec: dict[str, Any],
    context: AgentContext,
) -> dict[str, object]:
    return check_planned_keyword_coverage(context.original_request, spec)


def validate_spec_tool(spec: dict[str, Any]) -> dict[str, object]:
    try:
        _validate(spec)
    except Exception as error:
        return {"ok": False, "error": str(error)}
    return {"ok": True, "error": None}

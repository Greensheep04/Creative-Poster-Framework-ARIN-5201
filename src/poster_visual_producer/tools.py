from __future__ import annotations
from dataclasses import asdict
from poster_framework import AgentRole, ToolRegistry, JSONValue
from .contracts import TextElement
from .renderer import measure_text_block


def build_tool_registry(fonts, generator) -> ToolRegistry:
    registry = ToolRegistry()

    def measure(element: dict[str, JSONValue]) -> dict[str, JSONValue]:
        layout = measure_text_block(TextElement.model_validate(element), fonts)
        return {
            "lines": layout.lines,
            "ink_bounds": layout.ink_bounds,
            "placements": layout.placements,
            "font_sha256": layout.font_sha256,
        }

    def capabilities() -> dict[str, JSONValue]:
        data = asdict(generator.capabilities)
        data["sizes"] = [list(size) for size in generator.capabilities.sizes]
        return data

    registry.register(
        "poster.measure_text_block",
        measure,
        description="Measure text using the exact registered renderer font and wrapping engine.",
        allowed_roles={AgentRole.layout_planner, AgentRole.visual_producer},
    )
    registry.register(
        "visual.generator_capabilities",
        capabilities,
        description="Read confirmed generator sizes and parameter support.",
        allowed_roles={AgentRole.visual_producer},
    )
    return registry

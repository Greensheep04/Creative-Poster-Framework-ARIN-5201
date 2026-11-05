from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .registration import AgentRegistration
from .types import Agent, AgentContext, AgentInput, AgentOutput, AgentRole, ContentFormat

LAYOUT_BRIEF_FORMAT = ContentFormat(name="poster.creative-brief", version="1")
POSTER_SPEC_FORMAT = ContentFormat(name="poster.poster-spec", version="1")
_PALETTE = ("#F6FAFB", "#123C55", "#3D7893")


def validate_poster_spec(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("PosterSpec must be a JSON object")
    if value.get("schema_version") != "0.1.0":
        raise ValueError("unsupported PosterSpec schema_version")
    canvas = value.get("canvas")
    if canvas != {"width": 720, "height": 1280, "color_mode": "RGB"}:
        raise ValueError("PosterSpec canvas must be 720x1280 RGB")
    elements = value.get("elements")
    if not isinstance(elements, list) or not elements:
        raise ValueError("PosterSpec elements must be a non-empty list")
    ids: set[str] = set()
    asset_plan = value.get("asset_plan")
    if not isinstance(asset_plan, list):
        raise ValueError("asset_plan must be a list")
    asset_ids = [item.get("asset_id") for item in asset_plan if isinstance(item, dict)]
    if len(asset_ids) != len(asset_plan) or len(asset_ids) != len(set(asset_ids)):
        raise ValueError("asset_plan entries require unique asset_id values")
    for element in elements:
        if not isinstance(element, dict) or not isinstance(element.get("id"), str):
            raise ValueError("each element must have an id")
        if element["id"] in ids:
            raise ValueError("element ids must be unique")
        ids.add(element["id"])
        if element.get("type") not in {"image", "text", "shape"}:
            raise ValueError("unsupported element type")
        if element["type"] == "image" and element.get("asset_id") not in asset_ids:
            raise ValueError("image element references an unknown asset")
        if element["type"] == "text" and not isinstance(element.get("text"), str):
            raise ValueError("text element must contain its immutable copy")
        box = element.get("box")
        if not isinstance(box, dict) or set(box) != {"x", "y", "width", "height"}:
            raise ValueError("each element requires a pixel box")
        x, y, width, height = (box[k] for k in ("x", "y", "width", "height"))
        if any(isinstance(n, bool) or not isinstance(n, int) for n in (x, y, width, height)):
            raise ValueError("box coordinates must be integers")
        if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 720 or y + height > 1280:
            raise ValueError("element box must be positive and within canvas")
    bindings = value.get("keyword_element_bindings")
    if not isinstance(bindings, list):
        raise ValueError("keyword_element_bindings must be a list")
    binding_ids: list[str] = []
    for binding in bindings:
        if not isinstance(binding, dict) or not binding.get("keyword_id") or not binding.get("element_ids"):
            raise ValueError("each keyword requires at least one element binding")
        if any(item not in ids for item in binding["element_ids"]):
            raise ValueError("keyword binding references an unknown element")
        binding_ids.append(binding["keyword_id"])
    if len(binding_ids) != len(set(binding_ids)):
        raise ValueError("keyword bindings must have unique keyword_id values")


def _brief_copy(brief: Mapping[str, Any]) -> tuple[str, str | None]:
    copy = brief.get("copy")
    if isinstance(copy, dict):
        title = copy.get("title") or copy.get("headline")
        subtitle = copy.get("subtitle")
    else:
        title = brief.get("headline") or brief.get("title")
        subtitle = brief.get("subtitle")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("brief must contain title or headline copy")
    if subtitle is not None and (not isinstance(subtitle, str) or not subtitle.strip()):
        raise ValueError("subtitle must be a non-empty string when provided")
    return title, subtitle


class LayoutPlannerAgent(Agent):
    """Deterministic, schema-checked baseline planner; replace plan() to add an LLM."""

    async def run(self, inputs: AgentInput, context: AgentContext) -> AgentOutput:
        if inputs.content_format != LAYOUT_BRIEF_FORMAT:
            raise ValueError("LayoutPlannerAgent requires poster.creative-brief@1")
        brief = inputs.content
        if not isinstance(brief, Mapping):
            raise ValueError("creative brief must be an object")
        title, subtitle = _brief_copy(brief)
        request_keywords = context.original_request.keywords
        raw_keywords = brief.get("keywords", brief.get("keyword_texts"))
        if raw_keywords is not None:
            if not isinstance(raw_keywords, list) or raw_keywords != [k.text for k in request_keywords]:
                raise ValueError("brief keywords must preserve original request text and order")
        elements: list[dict[str, Any]] = [
            {"id": "hero", "type": "image", "z": 1, "box": {"x": 36, "y": 260, "width": 648, "height": 850}, "asset_id": "hero_image", "fit": "cover", "focus_point_norm": [0.5, 0.5]},
            {"id": "title", "type": "text", "z": 2, "box": {"x": 36, "y": 64, "width": 648, "height": 152}, "copy_ref": "title", "text": title, "font_family_key": "noto_sans_sc", "font_weight": 700, "font_size_px": 72, "line_height_px": 90, "max_lines": 1, "align": "center", "color": _PALETTE[1]},
        ]
        if subtitle:
            elements.append({"id": "subtitle", "type": "text", "z": 3, "box": {"x": 36, "y": 1160, "width": 648, "height": 56}, "copy_ref": "subtitle", "text": subtitle, "font_family_key": "noto_sans_sc", "font_weight": 400, "font_size_px": 28, "line_height_px": 38, "max_lines": 1, "align": "center", "color": _PALETTE[1]})
        spec = {
            "schema_version": "0.1.0", "run_id": context.original_request.run_id,
            "spec_version": 1,
            "based_on": [item.model_dump(mode="json") for item in inputs.metadata.version_dependencies],
            "canvas": {"width": 720, "height": 1280, "color_mode": "RGB"},
            "template_id": "framed_hero_v1",
            "background": {"color": _PALETTE[0]},
            "design_tokens": {"palette": list(_PALETTE), "font_family_key": "noto_sans_sc", "safe_margin_px": 36},
            "asset_plan": [{"asset_id": "hero_image", "kind": "generated_image", "prompt": "Create a visual metaphor for: " + "、".join(k.text for k in request_keywords) + ". Leave clear space for typography; no text or logos.", "negative_prompt": "text, logo, watermark", "target_size": {"width": 648, "height": 850}, "subject_safe_region_norm": [0.05, 0.1, 0.9, 0.8], "required_keyword_ids": [k.keyword_id for k in request_keywords]}],
            "elements": elements,
            "keyword_element_bindings": [{"keyword_id": k.keyword_id, "element_ids": ["hero"], "layout_evidence": "included in the hero visual brief"} for k in request_keywords],
            "allowed_overlaps": [], "render_policy": {"text_overflow": "error", "missing_font": "error", "output_format": "PNG"},
        }
        if spec["run_id"] != inputs.metadata.identity.run_id:
            raise ValueError("brief and execution run_id must match")
        bound_ids = {item["keyword_id"] for item in spec["keyword_element_bindings"]}
        expected_ids = {item.keyword_id for item in request_keywords}
        if bound_ids != expected_ids:
            raise ValueError("every original keyword must be bound exactly once")
        validate_poster_spec(spec)
        return AgentOutput(content=spec, content_format=POSTER_SPEC_FORMAT)


def registration() -> AgentRegistration:
    return AgentRegistration(
        role=AgentRole.layout_planner,
        instance_factory=lambda config: LayoutPlannerAgent(),
        input_formats=frozenset({LAYOUT_BRIEF_FORMAT}),
        output_formats=frozenset({POSTER_SPEC_FORMAT}),
        output_validator=validate_poster_spec,
    )

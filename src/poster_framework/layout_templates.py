"""The three controlled layout templates and the deterministic text fitting.

Templates are a constrained design space, not an extra agent: each one fixes the
hero slot, the copy slots, the acceptable copy length and the overlaps that are
intentional.  A2 may pick a template and move within its limits; it may not
invent arbitrary geometry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .layout_errors import TextOverflowError
from .layout_fonts import (
    DEFAULT_FAMILY_KEY,
    FontRegistry,
    contrast_ratio,
    default_font_registry,
    measure_text_block,
    pick_text_color,
    relative_luminance,
)

DEFAULT_PALETTE = ("#F6FAFB", "#123C55", "#3D7893")
LIGHT_BACKGROUND = "#F6FAFB"
DARK_BACKGROUND = "#0F1B24"
OVERLAY_TEXT_COLOR = "#FFFFFF"
MIN_BODY_FONT_SIZE_PX = 24

_HEX_FIND = re.compile(r"#[0-9a-fA-F]{6}\b")
_PALETTE_KEYS = {"palette", "colors", "palette_direction", "design_tokens", "color"}


@dataclass(frozen=True)
class SlotBox:
    x: int
    y: int
    width: int
    height: int

    def as_json(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class HeroSlot:
    element_id: str
    asset_id: str
    box: SlotBox
    focus_point_norm: tuple[float, float]
    subject_safe_region_norm: tuple[float, float, float, float]
    z: int = 1


@dataclass(frozen=True)
class TextSlot:
    element_id: str
    copy_ref: str
    box: SlotBox
    max_font_size_px: int
    min_font_size_px: int
    max_lines: int
    align: str
    z: int
    color_role: str
    grow_bottom_px: int = 0
    font_weight: int = 400

    @property
    def available_height_px(self) -> int:
        return self.box.height + self.grow_bottom_px


@dataclass(frozen=True)
class LayoutTemplate:
    template_id: str
    summary: str
    background_role: str
    hero: HeroSlot
    text_slots: tuple[TextSlot, ...]
    allowed_overlaps: tuple[tuple[str, str], ...] = ()
    bleed: bool = False


_TEMPLATES: dict[str, LayoutTemplate] = {
    "framed_hero_v1": LayoutTemplate(
        template_id="framed_hero_v1",
        summary="Title above a framed hero image with generous margins.",
        background_role="light",
        hero=HeroSlot(
            element_id="hero",
            asset_id="hero_image",
            box=SlotBox(x=36, y=260, width=648, height=850),
            focus_point_norm=(0.5, 0.5),
            subject_safe_region_norm=(0.05, 0.1, 0.9, 0.8),
        ),
        text_slots=(
            TextSlot(
                element_id="title",
                copy_ref="title",
                box=SlotBox(x=36, y=64, width=648, height=140),
                max_font_size_px=72,
                min_font_size_px=44,
                max_lines=1,
                align="center",
                z=2,
                color_role="primary",
                grow_bottom_px=24,
                font_weight=700,
            ),
            TextSlot(
                element_id="subtitle",
                copy_ref="subtitle",
                box=SlotBox(x=36, y=1160, width=648, height=56),
                max_font_size_px=28,
                min_font_size_px=MIN_BODY_FONT_SIZE_PX,
                max_lines=2,
                align="center",
                z=3,
                color_role="primary",
                font_weight=400,
            ),
        ),
    ),
    "full_bleed_v1": LayoutTemplate(
        template_id="full_bleed_v1",
        summary="Full-canvas hero with copy over the lower image area.",
        background_role="dark",
        hero=HeroSlot(
            element_id="hero",
            asset_id="hero_image",
            box=SlotBox(x=0, y=0, width=720, height=1280),
            focus_point_norm=(0.5, 0.45),
            subject_safe_region_norm=(0.05, 0.05, 0.9, 0.75),
        ),
        text_slots=(
            TextSlot(
                element_id="title",
                copy_ref="title",
                box=SlotBox(x=48, y=880, width=624, height=200),
                max_font_size_px=96,
                min_font_size_px=56,
                max_lines=2,
                align="left",
                z=2,
                color_role="overlay",
                grow_bottom_px=8,
                font_weight=700,
            ),
            TextSlot(
                element_id="subtitle",
                copy_ref="subtitle",
                box=SlotBox(x=48, y=1104, width=624, height=120),
                max_font_size_px=36,
                min_font_size_px=MIN_BODY_FONT_SIZE_PX,
                max_lines=2,
                align="left",
                z=3,
                color_role="overlay",
                font_weight=400,
            ),
        ),
        allowed_overlaps=(("hero", "title"), ("hero", "subtitle")),
        bleed=True,
    ),
    "split_stack_v1": LayoutTemplate(
        template_id="split_stack_v1",
        summary="Full-width hero above a stacked copy panel.",
        background_role="light",
        hero=HeroSlot(
            element_id="hero",
            asset_id="hero_image",
            box=SlotBox(x=0, y=0, width=720, height=760),
            focus_point_norm=(0.5, 0.5),
            subject_safe_region_norm=(0.05, 0.05, 0.9, 0.85),
        ),
        text_slots=(
            TextSlot(
                element_id="title",
                copy_ref="title",
                box=SlotBox(x=48, y=808, width=624, height=168),
                max_font_size_px=68,
                min_font_size_px=44,
                max_lines=2,
                align="left",
                z=2,
                color_role="primary",
                grow_bottom_px=16,
                font_weight=700,
            ),
            TextSlot(
                element_id="subtitle",
                copy_ref="subtitle",
                box=SlotBox(x=48, y=1000, width=624, height=120),
                max_font_size_px=32,
                min_font_size_px=MIN_BODY_FONT_SIZE_PX,
                max_lines=2,
                align="left",
                z=3,
                color_role="primary",
                font_weight=400,
            ),
        ),
    ),
}


def template_ids() -> tuple[str, ...]:
    return tuple(_TEMPLATES)


def templates() -> dict[str, LayoutTemplate]:
    return dict(_TEMPLATES)


def get_template(template_id: str) -> LayoutTemplate:
    try:
        return _TEMPLATES[template_id]
    except KeyError as error:
        raise ValueError(
            f"unknown layout template {template_id!r}; "
            f"available: {', '.join(template_ids())}"
        ) from error


def describe_templates() -> list[dict[str, object]]:
    return [
        {
            "template_id": item.template_id,
            "summary": item.summary,
            "background_role": item.background_role,
            "bleed": item.bleed,
            "hero": {
                "element_id": item.hero.element_id,
                "asset_id": item.hero.asset_id,
                "box": item.hero.box.as_json(),
                "focus_point_norm": list(item.hero.focus_point_norm),
                "subject_safe_region_norm": list(item.hero.subject_safe_region_norm),
            },
            "text_slots": [
                {
                    "element_id": slot.element_id,
                    "copy_ref": slot.copy_ref,
                    "box": slot.box.as_json(),
                    "max_font_size_px": slot.max_font_size_px,
                    "min_font_size_px": slot.min_font_size_px,
                    "max_lines": slot.max_lines,
                    "align": slot.align,
                    "min_body_font_size_px": MIN_BODY_FONT_SIZE_PX,
                }
                for slot in item.text_slots
            ],
            "allowed_overlaps": [list(pair) for pair in item.allowed_overlaps],
        }
        for item in _TEMPLATES.values()
    ]


def _collect_hex(node: object, found: list[str]) -> None:
    if isinstance(node, str):
        found.extend(_HEX_FIND.findall(node))
    elif isinstance(node, Mapping):
        for key, value in node.items():
            if key in _PALETTE_KEYS:
                _collect_hex(value, found)
    elif isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
        for value in node:
            _collect_hex(value, found)


def resolve_palette(brief: Mapping[str, Any] | None) -> list[str]:
    """Concrete colors from the brief's palette direction, or the default set."""

    found: list[str] = []
    if brief:
        _collect_hex(brief, found)
    palette: list[str] = []
    for color in found:
        normalized = color.upper()
        if normalized not in palette:
            palette.append(normalized)
    for fallback in DEFAULT_PALETTE:
        if len(palette) >= 3:
            break
        if fallback not in palette:
            palette.append(fallback)
    return palette[:5]


def background_color(template: LayoutTemplate, palette: Sequence[str]) -> str:
    """Palette-driven background with a contrast guard.

    A light template keeps the lightest palette color as long as it is actually
    light; a dark template keeps the darkest one as long as it is actually dark.
    Otherwise the template's neutral fallback is used, so copy stays readable.
    """

    options = [color for color in palette if color]
    if not options:
        return LIGHT_BACKGROUND if template.background_role == "light" else DARK_BACKGROUND
    if template.background_role == "light":
        lightest = max(options, key=relative_luminance)
        return lightest if relative_luminance(lightest) >= 0.55 else LIGHT_BACKGROUND
    darkest = min(options, key=relative_luminance)
    return darkest if relative_luminance(darkest) <= 0.35 else DARK_BACKGROUND


def slot_text_color(slot: TextSlot, background: str, palette: Sequence[str]) -> str:
    if slot.color_role == "overlay":
        return OVERLAY_TEXT_COLOR
    return pick_text_color(background, palette)


def fit_text_slot(
    slot: TextSlot,
    text: str,
    *,
    color: str,
    font_family_key: str = DEFAULT_FAMILY_KEY,
    registry: FontRegistry | None = None,
) -> dict[str, Any]:
    """Find the largest admissible size for ``text`` inside ``slot``.

    Order of attempts follows the design note: keep the template geometry and
    the widest allowed box first, then reduce the font size down to the slot
    minimum.  Never truncate copy and never go below the minimum: an unfittable
    title is a contract error that must go back to the copy owner.
    """

    active_registry = registry or default_font_registry()
    face = active_registry.resolve(font_family_key, slot.font_weight, allow_nearest=True)
    last_size = slot.max_font_size_px
    for size in range(slot.max_font_size_px, slot.min_font_size_px - 1, -2):
        line_height = max(size, round(size * 1.3))
        measurement = measure_text_block(
            text,
            font_family_key=font_family_key,
            font_weight=face.weight,
            font_size_px=size,
            line_height_px=line_height,
            max_width_px=slot.box.width,
            max_lines=slot.max_lines,
            box_height_px=slot.available_height_px,
            registry=active_registry,
        )
        last_size = size
        if not measurement.fits:
            continue
        height = max(line_height, measurement.required_height_px)
        height = min(height, slot.available_height_px)
        return {
            "id": slot.element_id,
            "type": "text",
            "z": slot.z,
            "box": {
                "x": slot.box.x,
                "y": slot.box.y,
                "width": slot.box.width,
                "height": height,
            },
            "copy_ref": slot.copy_ref,
            "text": text,
            "font_family_key": font_family_key,
            "font_weight": face.weight,
            "font_size_px": size,
            "line_height_px": line_height,
            "max_lines": slot.max_lines,
            "align": slot.align,
            "color": color,
        }
    raise TextOverflowError(
        f"copy for element {slot.element_id!r} does not fit "
        f"{slot.box.width}x{slot.available_height_px}px at the minimum size "
        f"{slot.min_font_size_px}px (last attempt {last_size}px, "
        f"max {slot.max_lines} lines)"
    )


def build_hero_element(template: LayoutTemplate) -> dict[str, Any]:
    hero = template.hero
    return {
        "id": hero.element_id,
        "type": "image",
        "z": hero.z,
        "box": hero.box.as_json(),
        "asset_id": hero.asset_id,
        "fit": "cover",
        "focus_point_norm": list(hero.focus_point_norm),
    }


def hero_asset_plan(
    template: LayoutTemplate,
    *,
    prompt: str,
    negative_prompt: str,
    keyword_ids: Sequence[str],
) -> dict[str, Any]:
    hero = template.hero
    return {
        "asset_id": hero.asset_id,
        "kind": "generated_image",
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "target_size": {"width": hero.box.width, "height": hero.box.height},
        "subject_safe_region_norm": list(hero.subject_safe_region_norm),
        "required_keyword_ids": list(keyword_ids),
    }


def text_contrast_ok(color: str, background: str, minimum: float = 4.5) -> bool:
    return contrast_ratio(color, background) >= minimum


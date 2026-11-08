from __future__ import annotations

import pytest

from layout_support import FONTS_AVAILABLE, make_brief, make_request
from poster_framework import (
    FontRegistry,
    TextOverflowError,
    default_font_registry,
    describe_templates,
    plan_poster_spec,
)
from poster_framework.layout_templates import (
    DEFAULT_PALETTE,
    background_color,
    fit_text_slot,
    get_template,
    resolve_palette,
    slot_text_color,
    template_ids,
    templates,
)

requires_fonts = pytest.mark.skipif(
    not FONTS_AVAILABLE, reason="no CJK-capable font installed"
)


def test_three_controlled_templates_are_registered() -> None:
    described = describe_templates()
    assert [item["template_id"] for item in described] == list(template_ids())
    assert set(template_ids()) == {"framed_hero_v1", "full_bleed_v1", "split_stack_v1"}


def test_template_slots_stay_inside_the_canvas_and_safe_margin() -> None:
    margin = 36
    for template in templates().values():
        hero = template.hero.box
        assert hero.x >= 0 and hero.y >= 0
        assert hero.x + hero.width <= 720
        assert hero.y + hero.height <= 1280
        for slot in template.text_slots:
            box = slot.box
            assert box.x >= margin, template.template_id
            assert box.y >= margin, template.template_id
            assert box.x + box.width <= 720 - margin, template.template_id
            assert box.y + box.height + slot.grow_bottom_px <= 1280 - margin
            assert slot.min_font_size_px >= 24
            assert slot.min_font_size_px <= slot.max_font_size_px
            assert slot.max_lines >= 1


def test_declared_overlaps_reference_real_slots() -> None:
    for template in templates().values():
        known = {template.hero.element_id} | {slot.element_id for slot in template.text_slots}
        for first, second in template.allowed_overlaps:
            assert first in known and second in known
            assert first != second
    assert get_template("full_bleed_v1").allowed_overlaps == (
        ("hero", "title"),
        ("hero", "subtitle"),
    )


def test_palette_comes_from_the_brief_selection() -> None:
    brief = {
        "palette": {
            "colors": [
                "deep ocean blue (#0b2a4c)",
                "faded turquoise (#A0D8E8)",
                "#0B2A4C",
            ]
        }
    }
    assert resolve_palette(brief) == ["#0B2A4C", "#A0D8E8", "#F6FAFB"]
    assert resolve_palette(None) == list(DEFAULT_PALETTE)


def test_background_and_text_colors_respect_contrast() -> None:
    light = get_template("framed_hero_v1")
    dark = get_template("full_bleed_v1")
    assert background_color(light, ["#0B2A4C", "#A0D8E8", "#F6FAFB"]) == "#F6FAFB"
    assert background_color(dark, ["#0B2A4C", "#A0D8E8", "#F6FAFB"]) == "#0B2A4C"
    title_slot = light.text_slots[0]
    assert slot_text_color(title_slot, "#F6FAFB", ["#0B2A4C", "#3D7893"]) == "#0B2A4C"
    overlay_slot = dark.text_slots[0]
    assert slot_text_color(overlay_slot, "#0B2A4C", ["#0B2A4C"]) == "#FFFFFF"


@requires_fonts
def test_fitting_keeps_the_largest_size_that_measures_clean() -> None:
    registry = default_font_registry()
    slot = get_template("framed_hero_v1").text_slots[0]
    short = fit_text_slot(slot, "静海", color="#0B2A4C", registry=registry)
    assert short["font_size_px"] == slot.max_font_size_px
    long = fit_text_slot(slot, "静海之蓝与漫长的宁静午后", color="#0B2A4C", registry=registry)
    assert long["font_size_px"] < short["font_size_px"]
    assert long["text"] == "静海之蓝与漫长的宁静午后"
    assert long["box"]["height"] <= slot.available_height_px


@requires_fonts
def test_unfittable_copy_is_an_error_not_a_truncation() -> None:
    slot = get_template("framed_hero_v1").text_slots[0]
    with pytest.raises(TextOverflowError):
        fit_text_slot(slot, "静" * 200, color="#0B2A4C")


@requires_fonts
def test_planned_spec_uses_the_measured_metrics() -> None:
    spec = plan_poster_spec(make_brief(copy={"title": "静海之蓝的宁静"}), request=make_request())
    title = next(element for element in spec["elements"] if element["id"] == "title")
    assert title["line_height_px"] >= title["font_size_px"]
    assert title["box"]["height"] >= title["font_size_px"]
    assert title["box"]["y"] == 64

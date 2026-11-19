from __future__ import annotations

import pytest

from layout_support import FONTS_AVAILABLE
from poster_framework import (
    FontRegistry,
    MissingFontError,
    contrast_ratio,
    default_font_registry,
    measure_text_block,
    pick_text_color,
)
from poster_framework.layout_fonts import can_break_between, is_cjk, wrap_text

requires_fonts = pytest.mark.skipif(
    not FONTS_AVAILABLE, reason="no CJK-capable font installed"
)


@requires_fonts
def test_registry_resolves_real_weights_and_files() -> None:
    registry = default_font_registry()
    regular = registry.resolve("noto_sans_sc", 400, allow_nearest=True)
    bold = registry.resolve("noto_sans_sc", 700, allow_nearest=True)
    assert regular.weight == 400
    assert bold.weight == 700
    assert regular.path.is_file() and bold.path.is_file()
    described = registry.describe()
    families = described["families"]
    assert isinstance(families, dict)
    assert "noto_sans_sc" in families
    entries = families["noto_sans_sc"]
    assert isinstance(entries, list)
    assert {entry["weight"] for entry in entries} == {400, 700}
    assert all(len(entry["sha256"]) == 64 for entry in entries)


@requires_fonts
def test_missing_weight_is_an_error_not_a_silent_substitution() -> None:
    registry = default_font_registry()
    with pytest.raises(MissingFontError):
        registry.resolve("noto_sans_sc", 100)
    with pytest.raises(MissingFontError):
        registry.resolve("font_that_does_not_exist", 400)


@requires_fonts
def test_nearest_weight_is_reported_truthfully() -> None:
    registry = default_font_registry()
    face = registry.resolve("noto_sans_sc", 900, allow_nearest=True)
    assert face.weight in registry.weights("noto_sans_sc")
    assert face.weight != 900


def test_cjk_and_latin_break_rules() -> None:
    assert is_cjk("海")
    assert not is_cjk("A")
    assert can_break_between("海", "洋")
    assert can_break_between("a", " ")
    assert not can_break_between("a", "b")
    assert can_break_between("，", "海")
    assert not can_break_between("海", "，")
    assert not can_break_between("（", "海")


@requires_fonts
def test_cjk_text_wraps_between_characters() -> None:
    registry = default_font_registry()
    font = registry.load("noto_sans_sc", 400, 40)
    lines = wrap_text("海洋宁静蓝色", font, 90)
    assert len(lines) > 1
    assert "".join(lines) == "海洋宁静蓝色"


@requires_fonts
def test_latin_words_are_not_split_when_they_fit() -> None:
    registry = default_font_registry()
    font = registry.load("noto_sans_sc", 400, 40)
    lines = wrap_text("calm ocean blue horizon", font, 260)
    assert len(lines) > 1
    assert " ".join(lines).split() == ["calm", "ocean", "blue", "horizon"]
    assert all(line.strip() == line and line for line in lines)


@requires_fonts
def test_measure_reports_overflow_against_width_height_and_lines() -> None:
    measurement = measure_text_block(
        "静海之蓝的宁静",
        font_size_px=72,
        line_height_px=94,
        max_width_px=50,
        max_lines=1,
        box_height_px=94,
    )
    assert measurement.fits_width is False
    assert measurement.fits is False
    assert measurement.missing_glyphs == ()

    roomy = measure_text_block(
        "静海之蓝",
        font_size_px=72,
        line_height_px=94,
        max_width_px=648,
        max_lines=1,
        box_height_px=152,
    )
    assert roomy.fits is True
    assert roomy.required_height_px <= 152
    assert roomy.widest_line_px <= 648


@requires_fonts
def test_measurement_is_deterministic() -> None:
    first = measure_text_block(
        "静海之蓝",
        font_size_px=64,
        line_height_px=84,
        max_width_px=500,
        max_lines=2,
    )
    second = measure_text_block(
        "静海之蓝",
        font_size_px=64,
        line_height_px=84,
        max_width_px=500,
        max_lines=2,
    )
    assert first.as_json() == second.as_json()


@requires_fonts
def test_missing_glyphs_are_detected_by_rendering() -> None:
    registry = default_font_registry()
    latin_weights = registry.weights("latin_sans")
    if not latin_weights:
        pytest.skip("no Latin-only font installed")
    face = registry.resolve("latin_sans", latin_weights[0])
    missing = registry.missing_glyphs(face, "海")
    assert missing == ("海",)
    assert registry.missing_glyphs(face, "sea") == ()


def test_contrast_and_palette_preference() -> None:
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0, abs=0.01)
    assert contrast_ratio("#123C55", "#123C55") == pytest.approx(1.0, abs=0.001)
    assert pick_text_color("#F6FAFB", ["#123C55", "#3D7893"]) == "#123C55"
    assert pick_text_color("#0B2A4C", ["#A0D8E8"]) == "#A0D8E8"
    assert pick_text_color("#000000") == "#FFFFFF"


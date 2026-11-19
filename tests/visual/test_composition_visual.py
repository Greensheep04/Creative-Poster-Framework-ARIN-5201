from poster_framework.composition import CompositionPlanningPolicy
from poster_framework.layout_fonts import measure_glyphs
from tests.layout.test_composition import context


def test_columns_preserve_the_full_source_without_claiming_subject_detection():
    import io

    from PIL import Image
    from poster_visual_producer.contracts import PosterSpec
    from poster_visual_producer.renderer import render_poster

    raw, req, registry, fonts = context()
    spec, evidence = CompositionPlanningPolicy().plan(raw, req, registry, "noto_sans_sc")
    assert spec["elements"][0]["fit"] == "contain"
    assert evidence["actual_subject_positions"] == "unknown"
    assert evidence["crop_requires_review"] is False
    source = io.BytesIO()
    Image.new("RGB", (1024, 768), "#536B55").save(source, "PNG")
    rendered = render_poster(PosterSpec.model_validate(spec), {"hero_image": source.getvalue()}, fonts)
    processing = rendered.manifest["elements"][0]["processing"]
    assert processing["crop_box"] is None
    assert processing["retained_area_ratio"] == 1.0


def test_english_article_is_not_stranded_as_its_own_headline_line():
    raw, req, registry, _ = context()
    raw["copy"]["headline"] = "Hope in the Ocean"
    spec, _ = CompositionPlanningPolicy().plan(raw, req, registry, "noto_sans_sc")
    t = spec["elements"][1]
    metrics = measure_glyphs(t["text"], registry.load("noto_sans_sc", 700, t["font_size_px"]), t["box"]["width"], t["line_height_px"])
    assert not any(s.strip().lower() in {"the", "a", "of", "in"} for s in metrics.lines)


def test_overlong_column_headline_uses_image_first_instead_of_four_small_lines():
    raw, req, registry, _ = context()
    raw["copy"]["headline"] = "Beneath the Cage, a Butterfly Flies"
    spec, evidence = CompositionPlanningPolicy().plan(raw, req, registry, "noto_sans_sc")
    assert evidence["relation"] == "image_first"
    assert spec["elements"][0]["box"]["y"] < spec["elements"][1]["box"]["y"]


def test_image_first_allocates_height_from_measured_copy_not_fixed_760px():
    raw, req, registry, _ = context("image_first")
    raw["copy"]["headline"] = "A Shared Future"
    spec, _ = CompositionPlanningPolicy().plan(raw, req, registry, "noto_sans_sc")
    assert spec["elements"][0]["box"]["height"] > 900
    assert max(e["box"]["y"] + e["box"]["height"] for e in spec["elements"] if e["type"] == "text") == 1244


def test_compact_cjk_headline_can_be_typography_led_when_columns_are_too_narrow():
    raw, req, registry, _ = context("asymmetric_left")
    raw["copy"]["headline"] = "情感可以被模拟，但孤独无法被替代"
    raw["copy"]["subtitle"] = "机器人学会微笑，却不懂为何我们哭泣"
    spec, evidence = CompositionPlanningPolicy().plan(raw, req, registry, "noto_sans_sc")
    assert evidence["relation"] == "typography_led"
    assert spec["elements"][1]["font_size_px"] >= 80


def test_qualified_avoid_does_not_negate_the_required_visible_flames():
    raw, req, registry, _ = context("image_first")
    raw["visual_direction"]["main_subject"] = "A wide forest with visible flames and a green sprout"
    raw["visual_requirements"] = {**raw["visual_direction"], "avoid": ["Close-up of flames", "Giant green stems", "watermark"]}
    raw["visual_direction"]["avoid"] = raw["visual_requirements"]["avoid"]
    spec, evidence = CompositionPlanningPolicy().plan(raw, req, registry, "noto_sans_sc")
    assert "flames" not in spec["asset_plan"][0]["negative_prompt"]
    assert "watermark" in spec["asset_plan"][0]["negative_prompt"]
    assert evidence["deferred_negative_preferences"]


def test_absolute_forbidden_subject_conflict_stops_before_generation():
    from poster_framework.layout_errors import LayoutContractError
    import pytest
    raw, req, registry, _ = context("image_first")
    raw["visual_requirements"] = {**raw["visual_direction"], "avoid": ["robot"]}
    raw["visual_direction"]["avoid"] = ["robot"]
    with pytest.raises(LayoutContractError, match="conflicts"):
        CompositionPlanningPolicy().plan(raw, req, registry, "noto_sans_sc")


def test_avoid_exception_beyond_seedling_does_not_negate_the_seedling():
    raw, req, registry, _ = context("image_first")
    raw["visual_direction"]["main_subject"] = "A green seedling before a burning forest"
    raw["visual_direction"]["avoid"] = ["symbolic representations beyond the seedling"]
    spec, evidence = CompositionPlanningPolicy().plan(raw, req, registry, "noto_sans_sc")
    assert "seedling" not in spec["asset_plan"][0]["negative_prompt"]
    assert evidence["deferred_negative_preferences"]

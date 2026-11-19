"""Behavioral tests for semantic planning; no model or GPU calls."""
import copy
import io

import pytest
from PIL import Image

from poster_framework import OriginalRequest
from poster_framework.layout_fonts import FontFace, FontRegistry
from poster_framework.layout_spec import parse_poster_spec
from poster_visual_producer.contracts import PosterSpec
from poster_visual_producer.renderer import render_poster
from tests.visual.helpers import fonts


def context(composition="asymmetric split; image right; copy left"):
    raw = {
        "copy": {"headline": "Feel the Silence", "subtitle": "A quiet connection.", "call_to_action": ""},
        "layout": {"composition": composition, "reading_order": "Image, then title", "typography": "sans-serif", "focal_point": "robot"},
        "visual_direction": {"main_subject": "One silver robot beside an empty chair", "style": "editorial photograph"},
        "palette": {"colors": ["#E9E4D9", "#183C3A", "#DE663D"]},
    }
    request = OriginalRequest.from_keywords("semantic-test", ["robot", "emotion", "loneliness"])
    f = fonts()
    registry = FontRegistry({(k, w): FontFace(k, w, face.path, face.index, "test") for k, weights in f.families.items() for w, face in weights.items()})
    return raw, request, registry, f


def plan(composition):
    from poster_framework.composition import CompositionPlanningPolicy
    raw, request, registry, f = context(composition)
    before = copy.deepcopy(raw)
    spec, evidence = CompositionPlanningPolicy().plan(raw, request, registry, "noto_sans_sc")
    assert raw == before
    return spec, evidence, f


def test_horizontal_intent_produces_real_columns_and_direction_changes_geometry():
    right, ev, _ = plan("asymmetric split; image right; copy left")
    left, _, _ = plan("asymmetric split; image left; copy right")
    assert right["template_id"] == "semantic_layout_v1"
    assert right["schema_version"] == "0.2.0"
    hero, title = right["elements"][:2]
    assert title["box"]["x"] + title["box"]["width"] <= hero["box"]["x"]
    assert left["elements"][0]["box"]["x"] < left["elements"][1]["box"]["x"]
    assert ev["relation"] == "side_by_side"
    assert ev["actual_subject_positions"] == "unknown"
    assert ev["candidate_count"] <= 8


def test_small_fitted_column_uses_image_first_without_changing_copy():
    from poster_framework.composition import CompositionPlanningPolicy
    raw, request, registry, _ = context("asymmetric_left")
    raw["copy"]["headline"] = "守护冰川，守护未来"
    spec, evidence = CompositionPlanningPolicy(source_size_for=lambda target: (768, 1024)).plan(
        raw, request, registry, "noto_sans_sc"
    )
    assert spec["template_id"] == "split_stack_v1"
    assert spec["elements"][0]["fit"] == "contain"
    assert spec["elements"][1]["text"] == raw["copy"]["headline"]
    assert not spec["allowed_overlaps"]
    assert evidence["planned_image_content_fraction"] >= .60
    assert any(attempt.get("rejection_code") == "image_area_below_floor" for attempt in evidence["candidate_attempts"])


def test_tall_source_can_keep_asymmetric_geometry_when_it_fills_the_column():
    from poster_framework.composition import CompositionPlanningPolicy
    raw, request, registry, _ = context("asymmetric_left")
    spec, evidence = CompositionPlanningPolicy(source_size_for=lambda target: (368, 1100)).plan(
        raw, request, registry, "noto_sans_sc"
    )
    assert spec["template_id"] == "semantic_layout_v1"
    assert evidence["planned_image_content_fraction"] > .40


def test_bundle_asymmetric_is_crop_free_and_keeps_public_template_id():
    spec, evidence, _ = plan("asymmetric_left")
    assert spec["template_id"] == "semantic_layout_v1"
    assert spec["elements"][0]["fit"] == "contain"
    assert spec["allowed_overlaps"] == []
    assert evidence["catalog_selection"] == "T3_ASYMMETRIC"
    assert evidence["crop_requires_review"] is False
    assert evidence["actual_subject_positions"] == "unknown"


@pytest.mark.parametrize("hint", ["before/after narrative contrast", "before_after"])
def test_bundle_narrative_contrast_keeps_single_artwork_without_unverified_overlay(hint):
    raw, request, registry, _ = context(hint)
    raw["visual_direction"]["main_subject"] = "An abandoned ruin on the left and thriving flowers on the right"
    from poster_framework.composition import CompositionPlanningPolicy
    spec, evidence = CompositionPlanningPolicy().plan(raw, request, registry, "noto_sans_sc")
    assert spec["template_id"] == "split_stack_v1"
    assert len(spec["asset_plan"]) == 1
    assert spec["allowed_overlaps"] == []
    assert evidence["catalog_selection"] == "T1_IMAGE_FIRST"
    assert evidence["deferred_overlay_template"] == "T5_NARRATIVE_CONTRAST"
    assert "One continuous scene" in spec["asset_plan"][0]["prompt"]


def test_bundle_explicit_full_bleed_preserves_pin_without_claiming_pixel_clearance():
    from poster_framework.composition import CompositionPlanningPolicy
    raw, request, registry, _ = context("before/after narrative contrast")
    spec, evidence = CompositionPlanningPolicy().plan(
        raw, request, registry, "noto_sans_sc", pinned_template="full_bleed_v1"
    )
    assert spec["template_id"] == "full_bleed_v1"
    assert spec["allowed_overlaps"]
    assert evidence["catalog_selection"] == "T2_FULL_BLEED"
    assert evidence["actual_contrast"] == "unknown"


def test_bundle_does_not_invent_a_negated_narrative_transition():
    from poster_framework.composition import CompositionPlanningPolicy
    raw, request, registry, _ = context("no before/after contrast")
    spec, evidence = CompositionPlanningPolicy().plan(raw, request, registry, "noto_sans_sc")
    assert "deferred_overlay_template" not in evidence
    assert "visible transition" not in spec["asset_plan"][0]["prompt"]


def test_bundle_records_overlay_deferral_even_when_framed_scores_higher():
    from poster_framework.composition import CompositionPlanningPolicy
    raw, request, registry, _ = context("overlay in negative space")
    raw["layout"].update(typography="Headline at top", reading_order="Headline at the top")
    spec, evidence = CompositionPlanningPolicy().plan(raw, request, registry, "noto_sans_sc")
    assert spec["template_id"] == "framed_hero_v1"
    assert evidence["deferred_overlay_template"] == "T2_FULL_BLEED"


def test_typography_led_changes_hierarchy_and_both_contracts_render():
    spec, ev, f = plan("typography-led; title is the dominant visual")
    assert ev["relation"] == "typography_led"
    assert spec["elements"][1]["font_size_px"] >= 80
    assert parse_poster_spec(spec).template_id == "semantic_layout_v1"
    asset = io.BytesIO()
    Image.new("RGB", (768, 1024), "#536B55").save(asset, "PNG")
    result = render_poster(PosterSpec.model_validate(spec), {"hero_image": asset.getvalue()}, f)
    assert Image.open(io.BytesIO(result.png)).size == (720, 1280)


def test_layout_prompt_uses_source_coordinates_and_preserves_essential_subject():
    spec, ev, _ = plan("asymmetric split; image right; copy left")
    prompt = spec["asset_plan"][0]["prompt"]
    assert "One silver robot beside an empty chair" in prompt
    assert "Feel the Silence" not in prompt
    # Copy is outside the image: do not incorrectly reserve half of the source.
    assert "right" in prompt.lower() or "toward" in prompt.lower()
    assert ev["prompt_layout_constraints"]
    assert spec["asset_plan"][0]["target_size"] == {k: spec["elements"][0]["box"][k] for k in ("width", "height")}


def test_long_copy_falls_back_without_rewriting_or_claiming_pixel_observations():
    from poster_framework.composition import CompositionPlanningPolicy
    raw, request, registry, _ = context()
    raw["copy"]["subtitle"] = "A quiet connection between people and machines. " * 3
    spec, ev = CompositionPlanningPolicy().plan(raw, request, registry, "noto_sans_sc")
    assert next(e for e in spec["elements"] if e["id"] == "subtitle")["text"] == raw["copy"]["subtitle"]
    assert ev["actual_subject_positions"] == "unknown"


def test_legacy_pin_is_respected_and_semantic_v1_mislabel_is_rejected():
    from poster_framework.composition import CompositionPlanningPolicy
    raw, request, registry, _ = context()
    spec, _ = CompositionPlanningPolicy().plan(raw, request, registry, "noto_sans_sc", pinned_template="split_stack_v1")
    assert spec["template_id"] == "split_stack_v1"
    semantic, _, _ = plan("typography-led")
    semantic["schema_version"] = "0.1.0"
    with pytest.raises(ValueError):
        PosterSpec.model_validate(semantic)


def test_config_exposes_explicit_opt_out():
    from poster_framework.project_config import A2
    assert A2().composition_aware is True
    assert A2(composition_aware=False).composition_aware is False

import copy
from types import SimpleNamespace

import pytest

from tests.layout.test_composition import context


def test_real_sdxl_model_identifier_enables_both_exact_tokenizers(monkeypatch):
    from examples.team_workflow import build_prompt_validator
    import poster_visual_producer.prompt_validation as validation
    sentinel = object()
    monkeypatch.setattr(validation, "SDXLPromptValidator", lambda root: sentinel)
    generator = SimpleNamespace(capabilities=SimpleNamespace(model="stabilityai/stable-diffusion-xl-base-1.0"), config=SimpleNamespace(model_dir="unused"))
    assert build_prompt_validator(generator, offline=False) is sentinel
    assert build_prompt_validator(generator, offline=True) is None


def test_leading_canonical_hint_wins_and_framed_is_executable():
    from poster_framework.composition import CompositionPlanningPolicy
    raw, req, registry, _ = context("framed; generous negative space for the concept")
    raw["layout"]["reading_order"] = "image first, then typography-led reflection"
    spec, evidence = CompositionPlanningPolicy().plan(raw, req, registry, "noto_sans_sc")
    assert spec["template_id"] == "framed_hero_v1"
    assert evidence["intended_relation"] == "framed"


def test_automatic_overlay_is_not_selected_without_source_observation():
    from poster_framework.composition import CompositionPlanningPolicy
    raw, req, registry, _ = context("overlay in negative space")
    raw["layout"]["typography"] = "overlay over the image"
    spec, _ = CompositionPlanningPolicy().plan(raw, req, registry, "noto_sans_sc")
    assert spec["template_id"] != "full_bleed_v1"
    assert spec["allowed_overlaps"] == []


def test_pinned_geometry_controls_prompt_not_original_lateral_hint():
    from poster_framework.composition import CompositionPlanningPolicy
    raw, req, registry, _ = context("asymmetric_left; image left")
    spec, evidence = CompositionPlanningPolicy().plan(raw, req, registry, "noto_sans_sc", pinned_template="framed_hero_v1")
    assert "toward the left" not in spec["asset_plan"][0]["prompt"]
    assert evidence["prompt_relation"] == "framed"


def test_image_first_does_not_invent_upward_motion():
    from poster_framework.composition import compile_composition_prompt
    raw, _, _, _ = context("image_first; a static portrait")
    prompt, _, _ = compile_composition_prompt(raw, "image_first", "center")
    assert "upward" not in prompt.lower()


def test_visual_prompt_overflow_has_a_different_failure_target_than_copy():
    from poster_framework.composition import compile_composition_prompt, VisualPromptOverflowError
    raw, _, _, _ = context()
    raw["visual_direction"]["main_subject"] = "required object " * 100
    with pytest.raises(VisualPromptOverflowError) as exc:
        compile_composition_prompt(raw, "image_first", "center", count_tokens=lambda text: [len(text.split())])
    assert exc.value.code == "VISUAL_PROMPT_OVERFLOW"


def test_cloud_sdxl_name_does_not_load_missing_local_tokenizer():
    from examples.team_workflow import build_prompt_validator
    generator = SimpleNamespace(capabilities=SimpleNamespace(model="provider/sdxl"), config=SimpleNamespace(endpoint="https://example.invalid"))
    assert build_prompt_validator(generator, offline=False) is None

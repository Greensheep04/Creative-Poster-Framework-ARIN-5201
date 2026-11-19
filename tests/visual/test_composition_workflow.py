import asyncio
import copy
import json

from examples.team_workflow import run_team


def test_live_prompt_selects_semantic_relation_and_controls_copy_capacity():
    from creative.prompt_loader import load_prompt
    prompt = load_prompt()
    assert "asymmetric_left" in prompt and "typography_led" in prompt
    assert "12 Chinese characters" in prompt
    assert "18 English words" in prompt


def test_integrated_backend_size_can_promote_image_first_before_a3(tmp_path, monkeypatch):
    from creative.creative_director import OfflineCreativeDirectorAgent
    original = OfflineCreativeDirectorAgent.run
    async def design(self, inputs, context):
        output = await original(self, inputs, context)
        raw = copy.deepcopy(output.content)
        raw["layout"]["composition"] = "asymmetric_left; absence creates distance"
        raw["copy"] = {"headline": "Quiet Machines", "subtitle": "A shared silence.", "call_to_action": ""}
        return output.model_copy(update={"content": raw})
    monkeypatch.setattr(OfflineCreativeDirectorAgent, "run", design)
    result = asyncio.run(run_team(tmp_path, ["robot", "emotion", "loneliness"], offline=True))
    assert result["image_operations"] == 1
    assert result["call_sequence"] == ["creative_director", "layout_planner", "visual_producer", "quality_critic"]
    saved = json.loads((tmp_path/"result.json").read_text("utf-8"))
    spec = saved["layout"]["content"]
    assert spec["template_id"] == "split_stack_v1"
    assert spec["elements"][0]["box"]["width"] == 720
    decision = json.loads(next(tmp_path.rglob("layout-decision.json")).read_text("utf-8"))
    assert decision["intended_relation"] == "side_by_side"
    assert decision["relation"] == "image_first"
    assert any(attempt.get("rejection_code") == "image_area_below_floor" for attempt in decision["candidate_attempts"])
    assert result["production_pass"] is False  # fixture evidence is never production success


def test_semantic_repair_preserves_generation_plan_and_unchanged_copy():
    from poster_framework.composition import CompositionPlanningPolicy, repair_composition
    from tests.layout.test_composition import context
    raw, req, registry, _ = context("typography-led")
    spec, _ = CompositionPlanningPolicy().plan(raw, req, registry, "noto_sans_sc")
    fixed, evidence = repair_composition(spec, [{"code": "text_contrast", "element_id": "title", "severity": "major"}], registry)
    assert fixed["asset_plan"] == spec["asset_plan"]
    assert fixed["elements"][0] == spec["elements"][0]
    assert [e["text"] for e in fixed["elements"] if e["type"] == "text"] == [e["text"] for e in spec["elements"] if e["type"] == "text"]
    assert evidence["repair_targets"] == ["title"]


def test_composition_prompt_never_discards_required_subject_for_style():
    from poster_framework.composition import compile_composition_prompt
    raw = {"visual_direction": {"main_subject": "A robot beside an empty chair", "style": "detailed elaborate cinematic style " * 50}}
    count = lambda text: [len(text.split()) + 2]
    prompt, _, deferred = compile_composition_prompt(raw, "side_by_side", "right", count_tokens=count)
    assert "A robot beside an empty chair" in prompt
    assert count(prompt)[0] <= 77
    assert deferred

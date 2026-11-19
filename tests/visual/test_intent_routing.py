import asyncio
import json
from copy import deepcopy

import pytest

from examples.team_workflow import run_team
from poster_framework.quality_layout import score_templates


def test_chinese_intent_preserves_image_first_reading_order():
    raw = {"layout": {"reading_order": "先看主体，再阅读底部标题", "composition": "垂直构图"},
           "copy": {"headline": "我们的海洋"}}
    assert score_templates(raw)[0]["template_id"] == "split_stack_v1"


def test_integrated_frame_matches_headline_intent_and_retains_cta(tmp_path, monkeypatch):
    from creative.creative_director import OfflineCreativeDirectorAgent
    original = OfflineCreativeDirectorAgent.run
    async def with_intent(self, inputs, context):
        result = await original(self, inputs, context)
        raw = deepcopy(result.content)
        raw["layout"]["reading_order"] = "Headline at top, then the main subject"
        raw["layout"]["composition"] = "Vertical portrait"
        raw["copy"] = {"headline": "A Shared Future", "subtitle": "Together we grow.", "call_to_action": "Join us"}
        return result.model_copy(update={"content": raw})
    monkeypatch.setattr(OfflineCreativeDirectorAgent, "run", with_intent)
    asyncio.run(run_team(tmp_path, ["ocean", "plastic", "hope"], offline=True))
    spec = json.loads((tmp_path / "result.json").read_text("utf-8"))["layout"]["content"]
    assert spec["template_id"] == "framed_hero_v1"
    assert {e.get("copy_ref") for e in spec["elements"]} >= {"title", "subtitle", "cta"}
    assert list(tmp_path.rglob("layout-decision.json"))


@pytest.mark.parametrize(("code", "expected"), [
    ("keyword_missing", "regenerate_asset"), ("subtitle_irregular_spacing", "revise_layout"),
    ("text_mismatch", "revise_layout"), ("copy_too_long", "revise_brief"),
    ("render_corrupt", "rerender"), ("unclassified_defect", None),
])
def test_directed_repair(code, expected):
    from poster_visual_producer.team_repair import repair_action
    report = {"decision": "fail", "issues": [{"severity": "major", "code": code}]}
    assert repair_action(report) == expected


def test_preflight_returns_oversized_copy_to_a1_before_first_image(tmp_path, monkeypatch):
    from creative.creative_director import OfflineCreativeDirectorAgent
    original = OfflineCreativeDirectorAgent.run
    async def write(self, inputs, context):
        output = await original(self, inputs, context)
        raw = deepcopy(output.content)
        raw["copy"]["headline"] = "Bright ocean" if context.metadata.revision_action else "Bright ocean " * 70
        return output.model_copy(update={"content": raw})
    monkeypatch.setattr(OfflineCreativeDirectorAgent, "run", write)
    result = asyncio.run(run_team(tmp_path, ["ocean", "plastic", "hope"], offline=True, auto_repair=True))
    assert result["call_sequence"][:4] == ["creative_director", "layout_planner"] * 2
    assert result["image_operations"] == result["revision_rounds"] == 1
    assert list(tmp_path.rglob("layout-overflow.json"))

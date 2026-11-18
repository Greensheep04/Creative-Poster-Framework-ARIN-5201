import asyncio
import json
from copy import deepcopy
from pathlib import Path
import pytest
from examples.team_workflow import run_team, live_prerequisites


@pytest.mark.parametrize("repair,images,calls", [(None,1,1),("revise_brief",1,2),("revise_layout",1,2),("rerender",1,2),("regenerate_asset",2,2)])
def test_all_four_actual_roles_and_bounded_offline_repair(tmp_path, repair, images, calls):
    summary = asyncio.run(run_team(tmp_path, ["ocean", "plastic", "hope"], offline=True, offline_repair=repair))
    assert summary["status"] == "needs_review"
    assert summary["production_pass"] is False and summary["mode"] == "offline"
    assert summary["image_operations"] == images
    assert summary["critic_calls"] == calls
    assert summary["revision_rounds"] == (1 if repair else 0)
    assert Path(summary["poster"]).is_file()
    briefs = [json.loads(p.read_text(encoding="utf-8"))["content"] for p in tmp_path.rglob("agent-output.json") if p.parent.parent.name == "creative_director"]
    assert max(b["brief_version"] for b in briefs) == (2 if repair == "revise_brief" else 1)
    assert json.loads((tmp_path/"summary.json").read_text())["run_id"] == summary["run_id"]


def test_real_a1_revision_receives_raw_previous_brief_and_actual_feedback(monkeypatch, tmp_path):
    import creative.creative_director as cd
    raw = json.loads((Path(cd.__file__).parent/"resources/sample-output.json").read_text())["content"]
    raw["keywords"] = ["ocean", "plastic", "hope"]; raw["copy"]["headline"] = "One Ocean"; raw["copy"]["subtitle"] = "Shared Hope"; raw["copy"]["call_to_action"] = ""
    revised = deepcopy(raw); revised["copy"]["headline"] = "Our Ocean"
    requests = []
    async def model(provider, model, request_data, parameters):
        requests.append(deepcopy(request_data))
        return json.dumps(raw if len(requests) == 1 else revised)
    monkeypatch.setattr(cd, "call_hugging_face", model)
    monkeypatch.setattr(cd.OfflineCreativeDirectorAgent, "run", cd.CreativeDirectorAgent().run)
    summary = asyncio.run(run_team(tmp_path, raw["keywords"], offline=True, offline_repair="revise_brief"))
    assert len(requests) == 2
    assert summary["critic_calls"] == 2
    assert summary["model_calls"] == 5
    request = json.loads(requests[1]["messages"][1]["content"])
    assert request["revision"]["previous_brief"] == raw
    assert request["revision"]["feedback"]["report"]["decision"] == "unknown"
    assert summary["image_operations"] == 1
    result = json.loads((tmp_path/"result.json").read_text(encoding="utf-8"))
    assert result["layout"]["content"]["spec_version"] == 2
    final = json.loads(Path(summary["brief"]).read_text())["content"]
    assert final["brief_version"] == 2 and final["copy"]["title"]["text"] == "Our Ocean"


def test_missing_live_config_is_reported_without_calls(monkeypatch, tmp_path):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    missing = live_prerequisites(backend_config=None, critic_config=None)
    assert "HF_TOKEN" in str(missing) and "A4" in str(missing) and "A3" in str(missing)
    with pytest.raises(ValueError, match="HF_TOKEN"):
        asyncio.run(run_team(tmp_path, ["ocean", "hope"], offline=False))
    assert not (tmp_path/"store").exists()


def test_existing_output_is_never_overwritten(tmp_path):
    (tmp_path/"summary.json").write_text("preserve")
    with pytest.raises(ValueError, match="new|empty"):
        asyncio.run(run_team(tmp_path, ["ocean", "hope"], offline=True))
    assert (tmp_path/"summary.json").read_text() == "preserve"


@pytest.mark.parametrize("repair", [None, "revise_layout"])
@pytest.mark.parametrize("template", [None, "full_bleed_v1"])
def test_required_cta_is_measured_preserved_and_not_duplicated(monkeypatch, tmp_path, repair, template):
    import creative.creative_director as cd
    original = cd.OfflineCreativeDirectorAgent.run
    async def with_cta(self, inputs, context):
        output = await original(self, inputs, context)
        raw = deepcopy(output.content); raw["copy"]["call_to_action"] = "Protect Our Ocean"
        return output.model_copy(update={"content":raw})
    monkeypatch.setattr(cd.OfflineCreativeDirectorAgent,"run",with_cta)
    summary = asyncio.run(run_team(tmp_path,["ocean","plastic","hope"],offline=True,offline_repair=repair,
        constraints={"template_id":template} if template else None))
    result = json.loads((tmp_path/"result.json").read_text(encoding="utf-8"))
    texts = [e for e in result["layout"]["content"]["elements"] if e["type"] == "text"]
    assert [e["text"] for e in texts if e["copy_ref"] == "cta"] == ["Protect Our Ocean"]
    assert result["layout"]["content"]["spec_version"] == (2 if repair else 1)


def test_zero_revision_budget_stops_before_authorized_repair(tmp_path):
    summary = asyncio.run(run_team(tmp_path,["ocean","plastic","hope"],offline=True,
        offline_repair="revise_brief",max_revision_rounds=0))
    assert summary["revision_rounds"] == 0 and summary["critic_calls"] == 1
    assert "budget" in summary["reason"]


@pytest.mark.parametrize("constraints", [None, {"template_id":"full_bleed_v1"}])
def test_a1_design_and_explicit_template_reach_actual_a2(monkeypatch, tmp_path, constraints):
    import creative.creative_director as cd
    original = cd.OfflineCreativeDirectorAgent.run
    async def design(self, inputs, context):
        output = await original(self, inputs, context)
        raw = deepcopy(output.content); raw["copy"]["headline"] = "Ocean Hope"
        raw["palette"]["colors"] = ["#001122", "#EEDDCC"]
        raw["visual_direction"]["style"] = "Woodcut illustration"
        return output.model_copy(update={"content":raw})
    monkeypatch.setattr(cd.OfflineCreativeDirectorAgent,"run",design)
    asyncio.run(run_team(tmp_path,["ocean","plastic","hope"],offline=True,constraints=constraints))
    result = json.loads((tmp_path/"result.json").read_text(encoding="utf-8"))
    spec = result["layout"]["content"]
    assert "#001122" in spec["design_tokens"]["palette"]
    assert "Woodcut illustration" in spec["asset_plan"][0]["prompt"]
    if constraints: assert spec["template_id"] == "full_bleed_v1"

import asyncio
import json
import subprocess
import sys
from pathlib import Path
import pytest
from examples.team_workflow import run_from_project_config

ROOT=Path(__file__).resolve().parents[2]
SECRET="sk-unified-integration-sentinel-123456789"

def config_file(tmp_path, *, budget=6, secret=SECRET):
    text=(ROOT/"config/project.example.toml").read_text(encoding="utf-8")
    text=text.replace('api_key = ""',f'api_key = "{secret}"')
    text=text.replace('../resources/fonts/',str(ROOT/'resources/fonts').replace('\\','/')+'/')
    text=text.replace('max_model_calls = 6',f'max_model_calls = {budget}')
    path=tmp_path/"local.toml";path.write_text(text,encoding="utf-8");return path

def test_single_file_drives_offline_workflow_and_redacts_all_outputs(tmp_path,monkeypatch):
    monkeypatch.setenv("HF_TOKEN","ambient-value")
    import os
    config=config_file(tmp_path)
    root=tmp_path/"run"
    summary=asyncio.run(run_from_project_config(config,root=root))
    assert summary["status"]=="needs_review" and summary["critic_calls"]==1
    snapshot=json.loads((root/"effective-config.json").read_text(encoding="utf-8"))
    assert snapshot["agents"]["a1"]["api_key"]=="[REDACTED]"
    assert snapshot["runtime"]["output_root"]==str(root.resolve())
    for p in root.rglob("*.json"):
        assert SECRET not in p.read_text(encoding="utf-8"),p
    assert os.environ["HF_TOKEN"]=="ambient-value"

def test_configured_budget_stop_before_candidate_still_exports_result(tmp_path):
    config=config_file(tmp_path,budget=0)
    root=tmp_path/"run"
    summary=asyncio.run(run_from_project_config(config,root=root))
    assert summary["status"]=="needs_review" and summary["poster"] is None
    assert (root/"result.json").exists() and (root/"effective-config.json").exists()

def test_blank_central_keys_do_not_fall_back_to_environment(tmp_path,monkeypatch):
    config=config_file(tmp_path,secret="")
    monkeypatch.setenv("HF_TOKEN",SECRET);monkeypatch.setenv("A4_API_KEY",SECRET)
    with pytest.raises(ValueError) as err:
        asyncio.run(run_from_project_config(config,root=tmp_path/"run",offline=False))
    assert "agents.a1.api_key" in str(err.value) and SECRET not in str(err.value)
    assert not (tmp_path/"run"/"store").exists()

def test_cli_config_check_and_offline_mode_override(tmp_path):
    config=config_file(tmp_path,secret="")
    result=subprocess.run([sys.executable,"-m","examples.team_workflow","--config",str(config),"--live","--check"],cwd=ROOT,capture_output=True,text=True,encoding="utf-8")
    assert result.returncode==2 and "agents.a1.api_key" in result.stdout
    result=subprocess.run([sys.executable,"-m","examples.team_workflow","--config",str(config),"--offline","--check"],cwd=ROOT,capture_output=True,text=True,encoding="utf-8")
    assert result.returncode==0 and json.loads(result.stdout)["network_requests"]==0


@pytest.mark.parametrize("via_cli", [False,True])
def test_runtime_errors_redact_configured_key(tmp_path,monkeypatch,capsys,via_cli):
    import examples.team_workflow as runner
    config=config_file(tmp_path)
    async def fail(*args,**kwargs):raise ValueError("provider echoed "+SECRET)
    monkeypatch.setattr(runner,"run_team",fail)
    if via_cli:
        monkeypatch.setattr(sys,"argv",["team_workflow","--config",str(config)])
        with pytest.raises(SystemExit) as err:runner.main()
        assert err.value.code==2
        text=capsys.readouterr().err
    else:
        with pytest.raises(ValueError) as err:asyncio.run(runner.run_from_project_config(config))
        text=str(err.value)
    assert SECRET not in text and "[REDACTED]" in text


@pytest.mark.parametrize("decision,revision_rounds,critic_calls", [("fail",1,1),("unknown",0,1)])
def test_missing_visual_routes_to_a3_and_identical_pixels_stop_before_repeat_review(tmp_path,monkeypatch,decision,revision_rounds,critic_calls):
    import examples.team_workflow as runner
    from examples.visual.team_integration import UnknownObservationProvider
    from poster_critic.ports import ModelResponse
    class FailedObservationProvider(UnknownObservationProvider):
        async def invoke(self,audit,prompt):
            response=await super().invoke(audit,prompt)
            observation=json.loads(response.text)
            observation["keyword_checks"]=[{"keyword_id":k.keyword_id,"status":"fail","evidence":"Controlled missing visual keyword."} for k in audit.keywords]
            observation["text_checks"]=[{"copy_id":c.copy_id,"observed_text":c.text,"status":"pass","readability":"pass"} for c in audit.approved_copy]
            observation["visual_checks"]=[{"check_id":key,"status":"pass","evidence":"Controlled layout observation."} for key in ("text_readability","layout_quality")]
            observation["uncertainties"]=[]
            return ModelResponse(text=json.dumps(observation))
    if decision=="fail":monkeypatch.setattr(runner,"UnknownObservationProvider",FailedObservationProvider)
    config=config_file(tmp_path)
    config.write_text(config.read_text(encoding="utf-8").replace('auto_repair = false','auto_repair = true'),encoding="utf-8")
    root=tmp_path/"run"
    summary=asyncio.run(run_from_project_config(config,root=root))
    assert summary["revision_rounds"]==revision_rounds
    assert summary["critic_calls"]==critic_calls
    assert summary["status"]=="needs_review" and summary["production_pass"] is False
    roles=["creative_director","layout_planner","visual_producer","quality_critic"]
    assert summary["call_sequence"]==roles + (["visual_producer"] if revision_rounds else [])
    if revision_rounds:
        assert "unchanged" in summary["reason"]
        auths=list(root.rglob("repair-authorization.json"))
        assert len(auths)==1
        auth=json.loads(auths[0].read_text(encoding="utf-8"))
        assert auth["authorization"]["action"]=="regenerate_asset"
        assert auth["report_decision"]=="fail"
        briefs=[json.loads(p.read_text(encoding="utf-8"))["content"]["brief_version"] for p in root.glob("store/*/invocations/creative_director/*/agent-output.json")]
        assert briefs==[1]
        assert len(list(root.glob("store/*/invocations/quality_critic/*/audit-report.json")))==1
    snapshot=json.loads((root/"effective-config.json").read_text(encoding="utf-8"))
    assert snapshot["runtime"]["auto_repair"] is True

import asyncio
import copy
import json
import pytest
from poster_framework import AgentRegistration, AgentRole, AgentOutput, AgentSet, PosterWorkflow, WORKFLOW_REQUEST_FORMAT, ExecutionMode
from poster_visual_producer.registration import BRIEF_FORMAT, registration, project_budget
from poster_visual_producer.generators import FixtureImageGenerator
from tests.visual.helpers import fonts, png
from tests.visual.test_qc_bridge import produced, report_for
from examples.visual.workflow_demo import DemoCreativeDirector


@pytest.mark.parametrize("repair", [None, "rerender", "regenerate_asset"])
@pytest.mark.parametrize("template", ["framed_hero_v1", "full_bleed_v1", "split_stack_v1"])
@pytest.mark.parametrize("language", ["zh", "en"])
def test_remote_a2_a3_a4_pipeline_preserves_copy_and_accounts_calls(tmp_path, template, language, repair):
    from poster_visual_producer.team_integration import layout_registration, critic_audit
    from poster_visual_producer.qc_bridge import qc_registration
    from poster_critic.ports import ModelResponse
    class ObservationProvider:
        name = "offline-observation-fixture"
        live = False
        async def invoke(self, audit, prompt):
            observation = {
                "keyword_checks": [{"keyword_id": k.keyword_id, "status": "pass", "evidence": "offline fixture"} for k in audit.keywords],
                "constraint_checks": [],
                "text_checks": [{"copy_id": c.copy_id, "observed_text": c.text, "status": "pass", "readability": "pass"} for c in audit.approved_copy],
                "visual_checks": [{"check_id": c, "status": "pass", "evidence": "offline fixture"} for c in ("text_readability", "layout_quality")],
                "issues": [], "uncertainties": ["offline fixture needs real inspection"] if repair else []}
            return ModelResponse(text=json.dumps(observation))
    observed = []
    audit = critic_audit(ObservationProvider(), max_retries=0)
    async def capture(path, context):
        report = await audit(path, context)
        observed.append(report)
        return report
    async def authorize(feedback, context):
        from poster_framework import RepairAuthorization, VersionDependency, RevisionAction
        if not repair or len(observed) > 1:
            return None
        c = feedback["current_candidate"]
        target = c["assets"][0]["source_ref"] if repair == "regenerate_asset" else c["candidate_ref"]
        return RepairAuthorization(
            authorization_id="offline-confirmed-repair",
            based_on_candidate=VersionDependency(artifact_id=c["candidate_id"], revision=c["candidate_ref"]["revision"]),
            action=RevisionAction(repair),
            target=VersionDependency(artifact_id=target["artifact_id"], revision=target["revision"]),
            expected_versions=c["based_on"], preserve=["title", "subtitle"])
    generation_count = [0]
    def source(request):
        generation_count[0] += 1
        return png("blue" if generation_count[0] == 1 else "green")
    registry = fonts()
    agents = AgentSet([
        AgentRegistration(role=AgentRole.creative_director, instance_factory=lambda c: DemoCreativeDirector(language), input_formats=frozenset({WORKFLOW_REQUEST_FORMAT}), output_formats=frozenset({BRIEF_FORMAT})),
        layout_registration(registry, font_family_key="noto_sans_sc", template_id=template),
        registration(FixtureImageGenerator(source), registry),
        qc_registration(capture, bundle_root=tmp_path/"reviews")])
    result = asyncio.run(PosterWorkflow(agents, root=tmp_path/"store", budget=project_budget(), repair_authorizer=authorize).run(["海洋", "宁静", "蓝色"], execution_mode=ExecutionMode.fake))
    assert result.status.value == "needs_review"  # Fake pass never releases.
    assert result.usage.image_operations == (2 if repair == "regenerate_asset" else 1)
    assert result.usage.critic_vision_calls == (2 if repair else 1)
    assert observed[0]["execution_mode"] == "fake"
    assert observed[0]["decision"] == ("unknown" if repair else "pass")
    candidate = json.loads(next((tmp_path/"reviews").rglob("candidate.json")).read_text(encoding="utf-8"))
    candidates = [json.loads(p.read_text(encoding="utf-8")) for p in (tmp_path/"reviews").rglob("candidate.json")]
    assert len(candidates) == (2 if repair else 1)
    if repair:
        assert candidates[0]["text_manifest"] == candidates[1]["text_manifest"]
        same_source = candidates[0]["assets"][0]["source_ref"] == candidates[1]["assets"][0]["source_ref"]
        assert same_source == (repair == "rerender")
    assert candidate["status"] == "rendered"
    assert candidate["text_manifest"][0]["text"] == ("静海之蓝" if language == "zh" else "OCEAN / STILLNESS")
    assert len(observed[0]["calls"]) == 1
    assert observed[0]["calls"][0]["billing_status"] == "not_applicable"


def test_current_a4_live_report_can_release(tmp_path, spec_data, brief_data):
    from poster_visual_producer.qc_bridge import export_review_bundle, validate_audit_report
    from poster_critic.contracts import AuditReportV3
    _, c, _, _, ctx = produced(tmp_path/"store", spec_data, brief_data)
    bundle = export_review_bundle(ctx, c, destination=tmp_path/"bundle")
    report = report_for(bundle, mode="live")
    report.pop("extra_record")
    report["calls"] = [{"attempt": 1, "model": "controlled-provider", "live": True, "status": "ok", "elapsed_ms": 1, "transport_status": "succeeded", "validation_status": "valid", "billing_status": "unknown"}]
    report = AuditReportV3.model_validate_json(json.dumps(report)).model_dump(mode="json")
    assert validate_audit_report(report, bundle, c).production_pass


def test_current_a4_string_repair_hint_is_retained(tmp_path, spec_data, brief_data):
    from poster_visual_producer.qc_bridge import export_review_bundle, validate_audit_report
    _, c, _, _, ctx = produced(tmp_path/"store", spec_data, brief_data)
    bundle = export_review_bundle(ctx, c, destination=tmp_path/"bundle")
    report = report_for(bundle, decision="fail", mode="live")
    report["repair_hint"] = {"stage": "layout", "element_id": "title", "asset_key": None, "reason": "declared overlap", "preserve": "Preserve approved copy and assets."}
    result = validate_audit_report(report, bundle, c)
    assert result.raw_report == report
    assert not result.production_pass


def test_critic_adapter_does_not_bypass_provider_mode(tmp_path, spec_data, brief_data):
    from poster_visual_producer.team_integration import critic_audit
    from poster_visual_producer.qc_bridge import export_review_bundle
    from poster_critic.providers.fake import FakeProvider
    _, c, _, _, ctx = produced(tmp_path/"store", spec_data, brief_data)
    bundle = export_review_bundle(ctx, c, destination=tmp_path/"bundle")
    with pytest.raises(Exception, match="mode must match"):
        asyncio.run(critic_audit(FakeProvider("{}"), max_retries=0)(bundle.manifest_path, ctx))


def test_distribution_includes_current_team_runtime():
    from pathlib import Path
    import tomllib
    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root/"pyproject.toml").read_text(encoding="utf-8"))
    assert "src/poster_critic" in config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    manifest = set((root/"scripts/package-manifest.txt").read_text(encoding="utf-8").splitlines())
    assert {"src/poster_visual_producer/team_integration.py", "src/poster_critic/engine.py", "src/poster_critic/prompts/critic_v4.txt", "src/poster_framework/layout_planner.py"} <= manifest

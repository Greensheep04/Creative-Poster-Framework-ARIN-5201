import asyncio, importlib.util, copy, io
import pytest
from PIL import Image
from poster_framework import (
    AgentRole,
    RevisionAction,
    VersionDependency,
    ArtifactPurpose,
    BudgetExceededError,
    RunBudget,
)


def api():
    assert importlib.util.find_spec("poster_visual_producer.agent"), (
        "Visual Producer Agent is required"
    )
    from poster_visual_producer.agent import VisualProducerAgent
    from poster_visual_producer.generators import (
        FixtureImageGenerator,
        CallableImageGenerator,
        GeneratorCapabilities,
        GeneratedImage,
        ProviderError,
    )
    from poster_visual_producer.registration import project_budget
    from tests.visual.helpers import make_session, call_context, fonts, png, publish

    return locals()


def setup(tmp_path, spec_data, brief_data):
    a = api()
    session, spec, brief = a["make_session"](
        tmp_path, spec_data, brief_data, a["project_budget"]()
    )
    producer = a["VisualProducerAgent"](
        a["FixtureImageGenerator"](lambda r: a["png"]()), a["fonts"]()
    )
    return a, session, spec, brief, producer


def run(a, producer, session, spec, invocation, action=None, target=None):
    inputs, ctx = a["call_context"](session, spec, invocation, action, target)
    return asyncio.run(producer.run(inputs, ctx))


def test_real_candidate_and_manifest(tmp_path, spec_data, brief_data):
    a, s, sp, b, p = setup(tmp_path, spec_data, brief_data)
    out = run(a, p, s, sp, "first")
    assert out.content["status"] == "rendered"
    candidates = [r for r in out.artifacts if r.purpose == ArtifactPurpose.candidate]
    assert len(candidates) == 1
    assert Image.open(io.BytesIO(s.file_store.read(candidates[0]))).size == (720, 1280)
    assert (
        out.content["usage_delta"]["image_ops"] == 1 and s.usage.image_operations == 1
    )
    assert out.content["source_mode"] == "offline_fixture"
    assert out.review is None


def test_title_move_reuses_asset_without_image_call(tmp_path, spec_data, brief_data):
    a, s, sp, b, p = setup(tmp_path, spec_data, brief_data)
    first = run(a, p, s, sp, "first")
    before = s.usage.image_operations
    sp = copy.deepcopy(sp)
    sp["spec_version"] = 2
    sp["elements"][1]["box"]["y"] = 84
    a["publish"](s, "layout", sp, AgentRole.layout_planner, "layout2")
    second = run(a, p, s, sp, "second")
    assert s.usage.image_operations == before
    assert second.content["usage_delta"]["image_ops"] == 0
    assert (
        second.content["assets"][0]["source_ref"]["sha256"]
        == first.content["assets"][0]["source_ref"]["sha256"]
    )
    assert second.content["assets"][0]["cache_hit"] is True


def test_rerender_uses_existing_assets_and_new_candidate(
    tmp_path, spec_data, brief_data
):
    a, s, sp, b, p = setup(tmp_path, spec_data, brief_data)
    first = run(a, p, s, sp, "first")
    target = VersionDependency(
        artifact_id=first.content["candidate_id"],
        revision=first.content["candidate_ref"]["revision"],
    )
    second = run(a, p, s, sp, "second", RevisionAction.rerender, target)
    assert s.usage.image_operations == 1
    assert first.content["candidate_id"] != second.content["candidate_id"]
    assert (
        first.content["candidate_ref"]["sha256"]
        == second.content["candidate_ref"]["sha256"]
    )


def test_targeted_regeneration_preserves_copy_and_layout(
    tmp_path, spec_data, brief_data
):
    a, s, sp, b, p = setup(tmp_path, spec_data, brief_data)
    first = run(a, p, s, sp, "first")
    raw = first.content["assets"][0]["source_ref"]
    target = VersionDependency(artifact_id=raw["artifact_id"], revision=raw["revision"])
    p.generator = a["FixtureImageGenerator"](lambda r: a["png"]("teal"))
    second = run(a, p, s, sp, "second", RevisionAction.regenerate_asset, target)
    assert s.usage.image_operations == 2
    assert second.content["assets"][0]["asset_version"] == 2
    assert first.content["text_manifest"] == second.content["text_manifest"]
    assert second.content["assets"][0]["source_ref"]["sha256"] != raw["sha256"]
    record = s.file_store.generation_record(
        next(x for x in second.artifacts if x.purpose == ArtifactPurpose.asset)
    )
    assert record.replaces == target


def test_stale_repair_refuses_before_generation(tmp_path, spec_data, brief_data):
    a, s, sp, b, p = setup(tmp_path, spec_data, brief_data)
    first = run(a, p, s, sp, "first")
    raw = first.content["assets"][0]["source_ref"]
    target = VersionDependency(artifact_id=raw["artifact_id"], revision="old-version")
    with pytest.raises(ValueError):
        run(a, p, s, sp, "second", RevisionAction.regenerate_asset, target)
    assert s.usage.image_operations == 1


def test_missing_font_fails_before_image_call(tmp_path, spec_data, brief_data):
    a, s, sp, b, p = setup(tmp_path, spec_data, brief_data)
    sp["elements"][1]["font_family_key"] = "missing"
    a["publish"](s, "layout", sp, AgentRole.layout_planner, "layout2")
    with pytest.raises(ValueError):
        run(a, p, s, sp, "first")
    assert s.usage.image_operations == 0


def test_budget_exhausted_does_not_invoke_provider(tmp_path, spec_data, brief_data):
    a = api()
    s, sp, b = a["make_session"](
        tmp_path, spec_data, brief_data, RunBudget(max_image_operations=0)
    )
    invoked = []
    p = a["VisualProducerAgent"](
        a["FixtureImageGenerator"](lambda r: invoked.append(r) or a["png"]()),
        a["fonts"](),
    )
    with pytest.raises(BudgetExceededError):
        run(a, p, s, sp, "first")
    assert not invoked and s.usage.image_operations == 0


@pytest.mark.parametrize("safe,expected_attempts", [(False, 1), (True, 2)])
def test_failures_counted_and_only_safe_retries(
    tmp_path, spec_data, brief_data, safe, expected_attempts
):
    a, s, sp, b, p = setup(tmp_path, spec_data, brief_data)

    def callback(req):
        raise a["ProviderError"](
            "offline fault",
            retry_safe=safe,
            submission_status="not_submitted" if safe else "unknown",
        )

    p.generator = a["CallableImageGenerator"](
        a["GeneratorCapabilities"]("sdk", "v1", ((1024, 1536),)), callback
    )
    with pytest.raises(a["ProviderError"]):
        run(a, p, s, sp, "first")
    assert s.usage.image_operations == expected_attempts
    assert len(s.model_events) == expected_attempts and all(
        e.status == "failed" for e in s.model_events
    )
    assert not any(
        x.purpose == ArtifactPurpose.candidate for x in s.file_store.artifacts
    )


def test_multiple_assets_repair_only_target(tmp_path, spec_data, brief_data):
    a = api()
    accent = copy.deepcopy(spec_data["asset_plan"][0])
    accent.update(
        asset_id="accent_image",
        target_size={"width": 120, "height": 20},
        required_keyword_ids=[],
        prompt="blue accent",
    )
    spec_data["asset_plan"].append(accent)
    spec_data["elements"].append(
        {
            "id": "accent",
            "type": "image",
            "z": 4,
            "box": {"x": 36, "y": 224, "width": 120, "height": 20},
            "asset_id": "accent_image",
            "fit": "cover",
            "focus_point_norm": [0.5, 0.5],
        }
    )
    a, s, sp, b, p = setup(tmp_path, spec_data, brief_data)
    first = run(a, p, s, sp, "first")
    hero = first.content["assets"][0]["source_ref"]
    target = VersionDependency(
        artifact_id=hero["artifact_id"], revision=hero["revision"]
    )
    p.generator = a["FixtureImageGenerator"](lambda r: a["png"]("teal"))
    second = run(a, p, s, sp, "second", RevisionAction.regenerate_asset, target)
    assert s.usage.image_operations == 3
    assert (
        first.content["assets"][1]["source_ref"]
        == second.content["assets"][1]["source_ref"]
    )
    assert first.content["text_manifest"] == second.content["text_manifest"]


def test_model_version_change_invalidates_cache(tmp_path, spec_data, brief_data):
    a, s, sp, b, p = setup(tmp_path, spec_data, brief_data)
    first = run(a, p, s, sp, "first")
    p.generator = a["FixtureImageGenerator"](
        lambda r: a["png"]("teal"), model="fixture-v2"
    )
    second = run(a, p, s, sp, "second")
    assert (
        s.usage.image_operations == 2
        and second.content["assets"][0]["cache_hit"] is False
    )


def test_saved_run_reuses_verified_asset_after_reload(tmp_path, spec_data, brief_data):
    from poster_framework import RunSession

    a, s, sp, b, p = setup(tmp_path, spec_data, brief_data)
    first = run(a, p, s, sp, "first")
    s.save(tmp_path / "record.json")
    restored = RunSession.load(tmp_path / "record.json", root=tmp_path)
    second = run(a, p, restored, sp, "restored")
    assert restored.usage.image_operations == 1
    assert (
        first.content["assets"][0]["source_ref"]
        == second.content["assets"][0]["source_ref"]
    )


def test_design_tokens_persist_but_nested_credentials_remain_redacted(tmp_path):
    from poster_framework import OriginalRequest, RunSession

    session = RunSession(
        OriginalRequest.from_keywords("redaction-test", ["ocean", "blue"]),
        root=tmp_path,
        configuration={
            "design_tokens": {
                "palette": ["#123C55"],
                "api_key": "secret-value",
                "nested": {"password": "hidden"},
            }
        },
    )
    record = session.save(tmp_path / "safe.json")
    assert record.configuration["design_tokens"]["palette"] == ["#123C55"]
    assert record.configuration["design_tokens"]["api_key"] == "[REDACTED]"
    assert record.configuration["design_tokens"]["nested"]["password"] == "[REDACTED]"


def test_rerender_preserves_repaired_asset_constraints(tmp_path, spec_data, brief_data):
    a, s, sp, b, p = setup(tmp_path, spec_data, brief_data)
    first = run(a, p, s, sp, "first")
    raw = first.content["assets"][0]["source_ref"]
    inputs, ctx = a["call_context"](s, sp, "repair-context")
    s.memory.add_revision(
        role=None,
        revision_id="approved-repair",
        changes={
            "details": {
                "repair_plan": {
                    "expected_versions": {"brief_version": 1, "spec_version": 1},
                    "constraints": ["Fix duplicate horizon"],
                }
            }
        },
        unresolved_issues=[],
    )
    target = VersionDependency(artifact_id=raw["artifact_id"], revision=raw["revision"])
    repaired = run(a, p, s, sp, "repair", RevisionAction.regenerate_asset, target)
    target = VersionDependency(
        artifact_id=repaired.content["candidate_id"],
        revision=repaired.content["candidate_ref"]["revision"],
    )
    rerendered = run(a, p, s, sp, "rerender", RevisionAction.rerender, target)
    assert s.usage.image_operations == 2
    assert (
        rerendered.content["assets"][0]["source_ref"]
        == repaired.content["assets"][0]["source_ref"]
    )


def test_provider_metadata_credentials_not_written_to_artifacts(
    tmp_path, spec_data, brief_data
):
    import json

    a, s, sp, b, p = setup(tmp_path, spec_data, brief_data)

    def callback(req):
        return a["GeneratedImage"](
            a["png"](),
            usage={"images": 1, "api_key": "usage-secret"},
            provenance={
                "api_key": "provider-secret",
                "nested": {"authorization": "auth-secret"},
            },
        )

    p.generator = a["CallableImageGenerator"](
        a["GeneratorCapabilities"]("sdk", "v1", ((1024, 1536),)), callback
    )
    output = run(a, p, s, sp, "first")
    text = json.dumps(output.content)
    for ref in output.artifacts:
        if ref.media_type == "application/json":
            text += s.file_store.read(ref).decode()
    assert (
        "usage-secret" not in text
        and "provider-secret" not in text
        and "auth-secret" not in text
    )
    assert output.content["assets"][0]["provenance"]["provider_usage"]["images"] == 1

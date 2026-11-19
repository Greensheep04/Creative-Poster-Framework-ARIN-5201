import asyncio
from copy import deepcopy
import importlib
from importlib.resources import files
import io
import json

from PIL import Image
import pytest

import poster_framework as framework


LAYOUT_FORMAT = framework.ContentFormat(name="test.revision-layout", version="1")
VISUAL_FORMAT = framework.ContentFormat(name="test.revision-visual", version="1")
REVIEW_FORMAT = framework.ContentFormat(name="test.revision-review", version="1")


@pytest.fixture
def director():
    return importlib.import_module("creative.creative_director")


@pytest.fixture
def briefs():
    resource = files("creative") / "resources" / "sample-output.json"
    initial = json.loads(resource.read_text(encoding="utf-8"))["content"]
    revised = deepcopy(initial)
    revised["copy"]["headline"] = "Pause Together, Share a Quiet Moment"
    return initial, revised


@pytest.fixture
def model_stub(monkeypatch, director):
    calls, replies = [], []

    async def invoke(provider, model, request_data, parameters):
        calls.append(deepcopy(request_data))
        if not replies:
            pytest.fail("Unexpected model invocation")
        return json.dumps(replies.pop(0))

    monkeypatch.setattr(director, "call_hugging_face", invoke)
    return calls, replies


def stored_reference(context, name):
    return next(
        item["reference"]
        for item in context.memory.context()["artifacts"]
        if item["name"] == name
    )


def user_request(model_request):
    return json.loads(next(
        message["content"]
        for message in model_request["messages"]
        if message["role"] == "user"
    ))


@pytest.mark.parametrize("feedback", [{"instruction": "Make the headline invite connection."}, None])
def test_workflow_routes_critic_feedback_through_real_director_revision(
    director, briefs, model_stub, tmp_path, feedback
):
    initial, revised = briefs
    calls, replies = model_stub
    replies.extend([initial, revised])
    consumed_briefs, reviewed_targets = [], []

    class Layout:
        async def run(self, inputs, context):
            consumed_briefs.append(deepcopy(inputs.content))
            return framework.AgentOutput(
                content={"headline": inputs.content["copy"]["headline"]},
                content_format=LAYOUT_FORMAT,
            )

    class Visual:
        async def run(self, inputs, context):
            image = io.BytesIO()
            Image.new("RGB", (720, 1280), "forestgreen").save(image, format="PNG")
            candidate = context.files.write_bytes(
                context.files.invocation_path("candidate.png"), image.getvalue(),
                purpose=framework.ArtifactPurpose.candidate, media_type="image/png",
            )
            return framework.AgentOutput(
                content=inputs.content, content_format=VISUAL_FORMAT, artifacts=(candidate,),
            )

    class Critic:
        async def run(self, inputs, context):
            first_review = not reviewed_targets
            candidate = stored_reference(context, "candidate")
            brief = stored_reference(context, "brief")
            target = framework.VersionDependency(
                artifact_id=brief["artifact_id"], revision=brief["revision"],
            )
            reviewed_targets.append(target)
            return framework.AgentOutput(
                content={"checked": True}, content_format=REVIEW_FORMAT,
                review=framework.ReviewRecord(
                    candidate_id=candidate["artifact_id"],
                    candidate_revision=candidate["revision"],
                    decision=(framework.ReviewDecision.fail if first_review else framework.ReviewDecision.pass_),
                    action=framework.RevisionAction.revise_brief if first_review else None,
                    target=target if first_review else None,
                    keyword_results=tuple(
                        framework.KeywordReviewResult(
                            keyword_id=keyword.keyword_id,
                            status=(
                                framework.KeywordReviewStatus.fail
                                if first_review and keyword.position == 0
                                else framework.KeywordReviewStatus.pass_
                            ),
                        )
                        for keyword in context.original_request.keywords
                    ),
                    details=feedback if first_review else "Accepted revised brief.",
                ),
            )

    def registration(role, agent, input_format, output_format):
        return framework.AgentRegistration(
            role=role, instance_factory=lambda config: agent,
            input_formats=frozenset({input_format}), output_formats=frozenset({output_format}),
        )

    workflow = framework.PosterWorkflow(
        framework.AgentSet([
            director.registration(),
            registration(framework.AgentRole.layout_planner, Layout(), director.BRIEF_FORMAT, LAYOUT_FORMAT),
            registration(framework.AgentRole.visual_producer, Visual(), LAYOUT_FORMAT, VISUAL_FORMAT),
            registration(framework.AgentRole.quality_critic, Critic(), VISUAL_FORMAT, REVIEW_FORMAT),
        ]),
        root=tmp_path,
        budget=framework.RunBudget(max_revision_rounds=1, max_model_calls=2),
    )

    result = asyncio.run(workflow.run(initial["keywords"], constraints={"tone": "calm"}))

    assert result.status.value == "passed"
    assert result.revision_rounds == 1
    assert result.call_sequence == tuple(framework.AgentRole) * 2
    assert len(calls) == result.usage.model_calls == 2
    assert len({event.identity.invocation_id for event in result.model_events}) == 2
    assert all(event.status == "succeeded" for event in result.model_events)
    original = result.original_request.model_dump(mode="json")
    assert user_request(calls[0]) == original
    revised_request = user_request(calls[1])
    assert revised_request.pop("revision") == {
        "previous_brief": initial,
        "feedback": feedback,
        "unresolved_issues": [result.original_request.keywords[0].keyword_id],
    }
    assert revised_request == original
    assert calls[0]["messages"][0]["content"] == director.load_prompt()
    assert calls[1]["messages"][0]["content"] == (
        director.load_prompt() + "\n\n" + director.load_revision_prompt()
    )
    assert consumed_briefs == [initial, revised]
    assert result.brief.content == revised
    assert result.layout.content["headline"] == revised["copy"]["headline"]
    assert reviewed_targets[0] != reviewed_targets[1]


@pytest.fixture
def revision_session(director, briefs, tmp_path):
    prior, _ = briefs
    request = framework.OriginalRequest.from_keywords("test-cd-revision", prior["keywords"])
    session = framework.RunSession(request, root=tmp_path)
    initial_identity = framework.ExecutionIdentity(
        run_id=request.run_id, role=framework.AgentRole.creative_director,
        invocation_id="initial-director", execution_mode=framework.ExecutionMode.real,
    )
    initial_context = session.create_context(initial_identity)
    reference = initial_context.files.write_bytes(
        initial_context.files.invocation_path("brief.json"), json.dumps(prior).encode(),
        purpose=framework.ArtifactPurpose.evidence, media_type="application/json",
    )
    initial_context.memory.publish_artifact("brief", reference, prior, director.BRIEF_FORMAT)
    target = framework.VersionDependency(artifact_id=reference.artifact_id, revision=reference.revision)
    return session, target


def add_feedback(session, target, details, *, name="review", action="revise_brief", issues=("headline",)):
    session.memory.add_revision(
        revision_id=name, role=None,
        changes={"action": action, "target": target.model_dump(mode="json"), "details": details},
        unresolved_issues=issues,
    )


def revision_call(director, session, target, *, action=framework.RevisionAction.revise_brief):
    identity = framework.ExecutionIdentity(
        run_id=session.run_id, role=framework.AgentRole.creative_director,
        invocation_id="revised-director", execution_mode=framework.ExecutionMode.real,
    )
    metadata = framework.FrameworkMetadata(
        identity=identity, original_request=session.original_request,
        revision_action=action, revision_target=target,
    )
    context = session.create_context(identity, metadata)
    inputs = framework.AgentInput(
        content=session.original_request.model_dump(mode="json"),
        content_format=framework.WORKFLOW_REQUEST_FORMAT, metadata=metadata,
    )
    return director.CreativeDirectorAgent().run(inputs, context)


@pytest.mark.parametrize("newest_matches", [True, False])
def test_revision_selects_latest_feedback_matching_target(
    director, briefs, model_stub, revision_session, newest_matches
):
    session, target = revision_session
    old_details = ["Clarify the headline", {"preserve": "palette"}]
    add_feedback(session, target, old_details, name="older", issues=("old-issue",))
    latest_target = target if newest_matches else target.model_copy(update={"revision": "stale"})
    add_feedback(session, latest_target, None, name="newer", issues=("new-issue",))
    calls, replies = model_stub
    replies.append(briefs[1])

    output = asyncio.run(revision_call(director, session, target))

    revision = user_request(calls[0])["revision"]
    assert revision["feedback"] == (None if newest_matches else old_details)
    assert revision["unresolved_issues"] == (["new-issue"] if newest_matches else ["old-issue"])
    assert revision["previous_brief"] == briefs[0]
    assert output.content == briefs[1]
    assert session.usage.model_calls == 1


@pytest.mark.parametrize(
    "problem",
    ["missing-target", "missing-brief", "no-feedback", "stale-artifact", "stale-revision", "wrong-history-action", "unsupported-action"],
)
def test_invalid_revision_context_fails_before_model_call(
    director, model_stub, revision_session, problem
):
    session, target = revision_session
    if problem != "no-feedback":
        add_feedback(
            session, target, "Fix the headline.",
            action="revise_layout" if problem == "wrong-history-action" else "revise_brief",
        )
    if problem == "missing-target":
        target = None
    elif problem == "missing-brief":
        session.memory.invalidate_artifact("brief")
    elif problem == "stale-artifact":
        target = target.model_copy(update={"artifact_id": "other-brief"})
    elif problem == "stale-revision":
        target = target.model_copy(update={"revision": "older-version"})
    action = (
        framework.RevisionAction.revise_layout
        if problem == "unsupported-action"
        else framework.RevisionAction.revise_brief
    )

    with pytest.raises(ValueError):
        asyncio.run(revision_call(director, session, target, action=action))

    assert model_stub[0] == []
    assert session.usage.model_calls == 0


@pytest.mark.parametrize("invalid_output", ["schema", "keywords"])
def test_revised_output_still_requires_valid_schema_and_original_keywords(
    director, briefs, model_stub, revision_session, invalid_output
):
    session, target = revision_session
    add_feedback(session, target, "Fix the headline.")
    revised = briefs[1]
    if invalid_output == "schema":
        del revised["copy"]["headline"]
    else:
        revised["keywords"] = ["unrelated", "replacement"]
    model_stub[1].append(revised)

    with pytest.raises(ValueError):
        asyncio.run(revision_call(director, session, target))

    assert session.usage.model_calls == 1
    assert len(model_stub[0]) == 1


@pytest.mark.parametrize("keywords", [None, [], ["", "", ""], [{"text":"green tea"}], {"unexpected":"metadata"}])
def test_revision_recovers_malformed_immutable_keywords_and_keeps_raw_evidence(director,briefs,model_stub,revision_session,tmp_path,keywords):
    session,target=revision_session
    add_feedback(session,target,"Fix image semantics.")
    revised=deepcopy(briefs[1]);revised["keywords"]=keywords
    model_stub[1].append(revised)
    output=asyncio.run(revision_call(director,session,target))
    assert output.content["keywords"]==briefs[0]["keywords"]
    assert output.content["copy"]==revised["copy"]
    raw=json.loads(next(tmp_path.rglob("model-response.json")).read_text(encoding="utf-8"))
    assert json.loads(raw["raw_text"])["keywords"]==keywords
    validation=json.loads(next(tmp_path.rglob("validation.json")).read_text(encoding="utf-8"))
    assert validation["status"]=="accepted" and validation["normalized_fields"]==["keywords"]
    assert session.usage.model_calls==1


@pytest.mark.parametrize("failure", ["json","schema"])
def test_rejected_revision_logs_prevalidation_response_and_masks_secrets(director,briefs,revision_session,tmp_path,monkeypatch,failure):
    session,target=revision_session
    add_feedback(session,target,"Fix image semantics.")
    secret="opaque-inline-credential-12345"
    monkeypatch.setenv("HF_TOKEN",secret)
    if failure=="json":
        raw='Broken JSON with '+secret+' hf_anotherSecret987 sk-cp-otherSecret987 Authorization: Bearer bearer-secret-987'
    else:
        invalid=deepcopy(briefs[1]);del invalid["copy"]["headline"]
        invalid["objective"]='Keep this diagnostic detail '+secret
        raw=json.dumps(invalid)
    async def respond(*args,**kwargs):return raw
    monkeypatch.setattr(director,"call_hugging_face",respond)
    with pytest.raises(ValueError):asyncio.run(revision_call(director,session,target))
    logged=json.loads(next(tmp_path.rglob("model-response.json")).read_text(encoding="utf-8"))
    assert logged["stage"]=="before_validation"
    assert "[REDACTED]" in logged["raw_text"]
    assert "Broken JSON" in logged["raw_text"] if failure=="json" else "Keep this diagnostic detail" in logged["raw_text"]
    for path in tmp_path.rglob("*.json"):
        value=path.read_text(encoding="utf-8")
        for token in (secret,"hf_anotherSecret987","sk-cp-otherSecret987","bearer-secret-987"):assert token not in value
    validation=json.loads(next(tmp_path.rglob("validation.json")).read_text(encoding="utf-8"))
    assert validation["status"]=="rejected" and validation["error_type"]=="ValueError"
    assert session.usage.model_calls==1

import asyncio
import hashlib
import json

import pytest

import poster_framework as framework
from poster_framework.fake import FAKE_CANDIDATE_MEDIA_TYPE, FakePosterModel


@pytest.mark.parametrize(
    ("initial_remaining_revisions", "expected_status", "expected_rounds", "expected_calls"),
    [
        (0, framework.WorkflowStatus.passed, 0, 4),
        (1, framework.WorkflowStatus.passed, 1, 6),
        (2, framework.WorkflowStatus.passed, 2, 8),
        (3, framework.WorkflowStatus.needs_review, 2, 8),
    ],
)
def test_fake_model_runs_four_scenarios_through_the_real_workflow(
    tmp_path,
    initial_remaining_revisions,
    expected_status,
    expected_rounds,
    expected_calls,
):
    model = FakePosterModel(
        initial_remaining_revisions=initial_remaining_revisions,
        delay_seconds=0,
    )
    workflow = framework.PosterWorkflow(
        model.agent_set(),
        root=tmp_path,
        budget=framework.RunBudget(max_revision_rounds=2),
    )

    result = asyncio.run(
        workflow.run(
            ["tea", "caffeine-free"],
            constraints={"tone": "calm"},
            execution_mode=framework.ExecutionMode.fake,
        )
    )

    expected_roles = [
        framework.AgentRole.creative_director,
        framework.AgentRole.layout_planner,
        framework.AgentRole.visual_producer,
        framework.AgentRole.quality_critic,
    ] + [
        role
        for _ in range(expected_rounds)
        for role in (
            framework.AgentRole.visual_producer,
            framework.AgentRole.quality_critic,
        )
    ]
    expected_remaining = max(0, initial_remaining_revisions - expected_rounds)
    expected_text = {
        0: "可以通过审核的图片",
        1: "需要修改的图片",
        2: "需要修改两次的图片",
        3: "需要修改三次的图片",
    }[expected_remaining]

    assert result.status == expected_status
    assert result.revision_rounds == expected_rounds
    assert result.call_sequence == tuple(expected_roles)
    assert result.usage.model_calls == expected_calls
    assert len(result.model_events) == expected_calls
    assert [event.identity.role for event in result.model_events] == expected_roles
    assert all(event.status == "succeeded" and event.executed for event in result.model_events)
    assert result.visual.content == {
        "image_text": expected_text,
        "remaining_revisions": expected_remaining,
    }
    assert result.candidate.media_type == FAKE_CANDIDATE_MEDIA_TYPE
    assert result.review.candidate_id == result.candidate.artifact_id
    assert result.review.candidate_revision == result.candidate.revision
    assert result.review.details["remaining_revisions"] == expected_remaining
    assert [call["role"] for call in model.calls] == [role.value for role in expected_roles]
    assert all("response" in call for call in model.calls)
    assert all(
        call["request_data"]["original_request"]
        == result.original_request.model_dump(mode="json")
        for call in model.calls
    )
    assert model.calls[0]["request_data"]["input"]["keywords"] == [
        keyword.model_dump(mode="json") for keyword in result.original_request.keywords
    ]
    assert model.calls[0]["request_data"]["original_request"]["constraints"] == [
        {"name": "tone", "value": "calm"}
    ]
    for call in model.calls:
        request_data = call["request_data"]
        model_request = request_data["model_request"]
        assert model_request["original_request"] == result.original_request.model_dump(
            mode="json"
        )
        private_messages = model_request["context"]["private_messages"]
        assert len(private_messages) == 1
        assert private_messages[0]["role"] == call["role"]
        assert private_messages[0]["invocation_id"] == request_data[
            "execution_identity"
        ]["invocation_id"]
        assert model_request["context"] == request_data["public_context"]

    record = framework.RunRecord.model_validate_json(
        result.record_path.read_text(encoding="utf-8")
    )
    candidates = [
        reference
        for reference in record.artifacts
        if reference.purpose == framework.ArtifactPurpose.candidate
    ]
    assert len(candidates) == expected_rounds + 1
    assert all(reference.uri.endswith("/candidate.json") for reference in candidates)
    session = framework.RunSession.load(result.record_path, root=tmp_path)
    for reference in candidates:
        data = session.file_store.read(reference)
        value = json.loads(data)
        assert value["remaining_revisions"] >= 0
        assert hashlib.sha256(data).hexdigest() == reference.sha256

    if initial_remaining_revisions == 3:
        assert [
            json.loads(session.file_store.read(reference))["remaining_revisions"]
            for reference in candidates
        ] == [3, 2, 1]
        assert result.reason == "revision round budget is exhausted"
        assert result.review.details["remaining_revisions"] == 1


def test_fake_model_text_candidate_is_rejected_in_real_execution_mode(tmp_path):
    model = FakePosterModel(initial_remaining_revisions=0, delay_seconds=0)
    workflow = framework.PosterWorkflow(model.agent_set(), root=tmp_path)

    with pytest.raises(framework.FileSecurityError):
        asyncio.run(workflow.run(["tea", "caffeine-free"]))

    record_paths = list((tmp_path / "runs").glob("*/run-record.json"))
    assert len(record_paths) == 1
    record = framework.RunRecord.model_validate_json(
        record_paths[0].read_text(encoding="utf-8")
    )
    assert record.final_result["status"] == "failed"
    assert [event.identity.role for event in record.model_events] == [
        framework.AgentRole.creative_director,
        framework.AgentRole.layout_planner,
        framework.AgentRole.visual_producer,
    ]

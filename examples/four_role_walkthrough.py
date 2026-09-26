from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from poster_framework import (
    AgentRole,
    ArtifactPurpose,
    ExecutionMode,
    FAKE_CANDIDATE_MEDIA_TYPE,
    KeywordReviewStatus,
    ReviewDecision,
    RunRecord,
    RunSession,
    RevisionAction,
    WorkflowResult,
)
from poster_framework.demo import run_demo


EXPECTED_ROLES = [
    AgentRole.creative_director.value,
    AgentRole.layout_planner.value,
    AgentRole.visual_producer.value,
    AgentRole.quality_critic.value,
]
IMAGE_TEXT_BY_REMAINING = {
    0: "可以通过审核的图片",
    1: "需要修改的图片",
    2: "需要修改两次的图片",
    3: "需要修改三次的图片",
}
EXPECTED_STATUSES = ["passed", "passed", "passed", "needs_review"]


def validate_run_record(
    scenario: dict[str, object],
    *,
    root: Path,
) -> dict[str, object]:
    record_path = Path(scenario["record_path"])
    record = RunRecord.model_validate_json(record_path.read_text(encoding="utf-8"))
    if record.final_result is None:
        raise AssertionError("workflow record has no final result")
    result = WorkflowResult.model_validate(record.final_result)
    initial_remaining = scenario["initial_remaining_revisions"]
    rounds = scenario["revision_rounds"]
    remaining = initial_remaining - rounds
    expected_status = EXPECTED_STATUSES[initial_remaining]
    if result.original_request.run_id != scenario["run_id"]:
        raise AssertionError("run record identity does not match the demo report")
    if result.status.value != expected_status or result.revision_rounds != rounds:
        raise AssertionError("run record status does not match the scenario")
    expected_sequence = EXPECTED_ROLES + [
        role
        for _ in range(rounds)
        for role in (AgentRole.visual_producer.value, AgentRole.quality_critic.value)
    ]
    if [role.value for role in result.call_sequence] != expected_sequence:
        raise AssertionError("workflow call sequence does not match the routed roles")
    if result.brief is None or result.layout is None or result.visual is None:
        raise AssertionError("four-role run must publish brief, layout, and visual outputs")

    keyword_ids = [item.keyword_id for item in result.original_request.keywords]
    keyword_text = [item.text for item in result.original_request.keywords]
    expected_constraints = [
        item.model_dump(mode="json") for item in result.original_request.constraints
    ]
    expected_brief = {
        "headline": " + ".join(keyword_text),
        "copy": "、".join(keyword_text),
        "keyword_ids": keyword_ids,
        "constraints": expected_constraints,
    }
    if result.brief.content != expected_brief:
        raise AssertionError("creative_director output does not preserve the original request")
    expected_layout = {
        **expected_brief,
        "canvas": {"width": 720, "height": 1280},
        "blocks": ["headline", "copy"],
    }
    if result.layout.content != expected_layout:
        raise AssertionError("layout_planner output does not preserve the brief")
    expected_visual = {
        "image_text": IMAGE_TEXT_BY_REMAINING[remaining],
        "remaining_revisions": remaining,
    }
    if result.visual.content != expected_visual:
        raise AssertionError("visual_producer output does not match the routed revision count")
    if result.candidate is None or result.candidate.purpose != ArtifactPurpose.candidate:
        raise AssertionError("visual_producer did not register a candidate file")
    if result.candidate.media_type != FAKE_CANDIDATE_MEDIA_TYPE:
        raise AssertionError("Fake candidate must use its explicit test media type")
    if result.review is None:
        raise AssertionError("quality_critic did not publish a review")
    expected_decision = ReviewDecision.pass_ if remaining == 0 else ReviewDecision.fail
    if result.review.decision != expected_decision:
        raise AssertionError("quality_critic decision does not match the current candidate")
    if (
        result.review.candidate_id != result.candidate.artifact_id
        or result.review.candidate_revision != result.candidate.revision
    ):
        raise AssertionError("quality_critic review does not identify the current candidate")
    if [item.keyword_id for item in result.review.keyword_results] != keyword_ids:
        raise AssertionError("quality_critic review does not cover each original keyword once")
    if any(item.status != KeywordReviewStatus.pass_ for item in result.review.keyword_results):
        raise AssertionError("FakeModel review must return a status for each original keyword")
    if remaining > 0:
        if result.review.action != RevisionAction.rerender or result.review.target is None:
            raise AssertionError("failed review must identify the public rerender target")
        if (
            result.review.target.artifact_id != result.candidate.artifact_id
            or result.review.target.revision != result.candidate.revision
        ):
            raise AssertionError("rerender target must match the current candidate version")

    session = RunSession.load(record_path, root=root)
    reviewer_context = session.create_context(result.review.reviewer_identity)
    public_memory = reviewer_context.memory.context()
    revision_history = public_memory["revision_history"]
    if len(revision_history) != rounds:
        raise AssertionError("public memory does not contain each rerender instruction")
    revision_samples = []
    for index, history_item in enumerate(revision_history):
        changes = history_item["changes"]
        details = changes["details"]
        if changes["action"] != RevisionAction.rerender.value:
            raise AssertionError("public memory contains an unexpected revision action")
        if details["remaining_revisions"] != initial_remaining - index:
            raise AssertionError("public memory does not preserve the prior review details")
        revision_sample = {
            "revision_id": history_item["revision_id"],
            "action": changes["action"],
            "target": changes["target"],
            "candidate_id": changes["candidate_id"],
            "details": details,
            "unresolved_issues": history_item["unresolved_issues"],
        }
        reference = reviewer_context.memory.history_reference(history_item["revision_id"])
        if reviewer_context.memory.read_history(reference).revision_id != history_item["revision_id"]:
            raise AssertionError("public memory history reference returned another revision")
        revision_samples.append(revision_sample)

    return {
        "initial_remaining_revisions": initial_remaining,
        "run_id": result.run_id,
        "status": result.status.value,
        "revision_rounds": result.revision_rounds,
        "call_sequence": [role.value for role in result.call_sequence],
        "brief": result.brief.content,
        "layout": result.layout.content,
        "visual": result.visual.content,
        "candidate": result.candidate.model_dump(mode="json"),
        "review": result.review.model_dump(mode="json"),
        "revision_instructions": revision_samples,
        "public_unresolved_issues": public_memory["unresolved_issues"],
        "record_path": str(record_path),
    }


async def run_example(root: Path, delay_seconds: float) -> dict[str, object]:
    report = await run_demo(root, delay_seconds=delay_seconds)
    scenarios = report["scenarios"]
    if len(scenarios) != 4:
        raise AssertionError("walkthrough must include four revision budget scenarios")
    validated_scenarios = [
        validate_run_record(item, root=root)
        for item in scenarios
    ]
    if [item["status"] for item in validated_scenarios] != EXPECTED_STATUSES:
        raise AssertionError("walkthrough produced unexpected status for a budget scenario")
    return {
        "execution_mode": ExecutionMode.fake.value,
        "delay_seconds": report["delay_seconds"],
        "total_elapsed_seconds": report["total_elapsed_seconds"],
        "scenarios": validated_scenarios,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(".scratch/four-role-walkthrough"),
    )
    parser.add_argument("--delay-seconds", type=float, default=15)
    arguments = parser.parse_args()
    report = asyncio.run(run_example(arguments.root, arguments.delay_seconds))
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()

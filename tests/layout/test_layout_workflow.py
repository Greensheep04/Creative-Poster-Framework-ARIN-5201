from __future__ import annotations

import asyncio
import io
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

from layout_support import FONTS_AVAILABLE, make_brief
from poster_framework import (
    Agent,
    AgentContext,
    AgentInput,
    AgentOutput,
    AgentRegistration,
    AgentRole,
    AgentSet,
    ArtifactPurpose,
    ContentFormat,
    KeywordReviewResult,
    KeywordReviewStatus,
    PosterWorkflow,
    ReviewDecision,
    ReviewRecord,
    RevisionAction,
    RunBudget,
    VersionDependency,
    WorkflowStatus,
    WORKFLOW_REQUEST_FORMAT,
)
from poster_framework.layout_planner import BRIEF_FORMAT, POSTER_SPEC_FORMAT, registration

VISUAL_FORMAT = ContentFormat(name="poster.visual", version="1")
REVIEW_FORMAT = ContentFormat(name="poster.review", version="1")


def _stored(context: AgentContext, name: str) -> dict[str, object]:
    artifacts = context.memory.context()["artifacts"]
    return next(item["reference"] for item in artifacts if item["name"] == name)


class BriefAgent(Agent):
    async def run(self, inputs: AgentInput, context: AgentContext) -> AgentOutput:
        brief = make_brief()
        brief["keywords"] = [item.text for item in context.original_request.keywords]
        return AgentOutput(content=brief, content_format=BRIEF_FORMAT)


class ProducerAgent(Agent):
    def __init__(self, state: dict[str, list[object]]) -> None:
        self._state = state

    async def run(self, inputs: AgentInput, context: AgentContext) -> AgentOutput:
        if inputs.content_format != POSTER_SPEC_FORMAT:
            raise ValueError("producer requires poster.spec@0.1.0")
        spec = inputs.content
        self._state["specs"].append(spec)
        buffer = io.BytesIO()
        Image.new("RGB", (720, 1280), (18, 60, 85)).save(buffer, format="PNG")
        candidate = context.files.write_bytes(
            context.files.invocation_path("candidate.png"),
            buffer.getvalue(),
            purpose=ArtifactPurpose.candidate,
            media_type="image/png",
        )
        title = next(
            element["text"] for element in spec["elements"] if element["id"] == "title"
        )
        return AgentOutput(
            content={"title": title, "spec_version": spec["spec_version"]},
            content_format=VISUAL_FORMAT,
            artifacts=(candidate,),
        )


class CriticAgent(Agent):
    def __init__(self, state: dict[str, list[object]]) -> None:
        self._state = state

    async def run(self, inputs: AgentInput, context: AgentContext) -> AgentOutput:
        candidate = _stored(context, "candidate")
        first_review = not context.memory.context()["revision_history"]
        layout = _stored(context, "layout")
        review = ReviewRecord(
            candidate_id=candidate["artifact_id"],
            candidate_revision=candidate["revision"],
            decision=ReviewDecision.fail if first_review else ReviewDecision.pass_,
            action=RevisionAction.revise_layout if first_review else None,
            target=(
                VersionDependency(
                    artifact_id=layout["artifact_id"],
                    revision=layout["revision"],
                )
                if first_review
                else None
            ),
            keyword_results=tuple(
                KeywordReviewResult(
                    keyword_id=item.keyword_id,
                    status=(
                        KeywordReviewStatus.fail
                        if first_review and item.position == 0
                        else KeywordReviewStatus.pass_
                    ),
                )
                for item in context.original_request.keywords
            ),
            details={"instruction": "title sits too high"},
        )
        self._state["reviews"].append(review.model_dump(mode="json"))
        return AgentOutput(
            content={"checked": True},
            content_format=REVIEW_FORMAT,
            review=review,
        )


def _registrations(state: dict[str, list[object]]) -> AgentSet:
    return AgentSet(
        [
            AgentRegistration(
                role=AgentRole.creative_director,
                instance_factory=lambda config: BriefAgent(),
                input_formats=frozenset({WORKFLOW_REQUEST_FORMAT}),
                output_formats=frozenset({BRIEF_FORMAT}),
            ),
            registration(),
            AgentRegistration(
                role=AgentRole.visual_producer,
                instance_factory=lambda config: ProducerAgent(state),
                input_formats=frozenset({POSTER_SPEC_FORMAT}),
                output_formats=frozenset({VISUAL_FORMAT}),
            ),
            AgentRegistration(
                role=AgentRole.quality_critic,
                instance_factory=lambda config: CriticAgent(state),
                input_formats=frozenset({VISUAL_FORMAT}),
                output_formats=frozenset({REVIEW_FORMAT}),
            ),
        ]
    )


def _revision_validator(
    before: object,
    after: object,
    action: RevisionAction,
    request: object,
) -> None:
    if action == RevisionAction.revise_layout:
        before_title = next(
            item["text"] for item in before["layout"]["elements"] if item["id"] == "title"
        )
        after_title = next(
            item["text"] for item in after["layout"]["elements"] if item["id"] == "title"
        )
        if before_title != after_title:
            raise ValueError("layout revision must preserve the approved title")


@pytest.fixture()
def run_root() -> Path:
    # A repo-local scratch directory also works in sandboxes that deny system
    # temp writes; ``.scratch/`` is gitignored.
    root = Path(".scratch") / "layout-workflow" / uuid4().hex
    root.mkdir(parents=True)
    return root


@pytest.mark.skipif(not FONTS_AVAILABLE, reason="no CJK-capable font installed")
def test_layout_planner_runs_inside_the_four_role_workflow(run_root: Path) -> None:
    state: dict[str, list[object]] = {"specs": [], "reviews": []}
    workflow = PosterWorkflow(
        _registrations(state),
        root=run_root,
        budget=RunBudget(max_revision_rounds=1),
        revision_validator=_revision_validator,
    )
    result = asyncio.run(workflow.run(["海洋", "宁静", "蓝色"]))

    assert result.status == WorkflowStatus.passed
    assert result.revision_rounds == 1
    assert result.review is not None and result.review.decision == ReviewDecision.pass_
    specs = state["specs"]
    assert len(specs) == 2
    first, revised = specs
    assert first["spec_version"] == 1
    assert revised["spec_version"] == 2
    assert revised["template_id"] != first["template_id"]
    first_title = next(e for e in first["elements"] if e["id"] == "title")
    revised_title = next(e for e in revised["elements"] if e["id"] == "title")
    assert revised_title["text"] == first_title["text"]

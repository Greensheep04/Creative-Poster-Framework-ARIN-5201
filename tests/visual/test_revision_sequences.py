import asyncio, copy, io
from PIL import Image
import pytest
from poster_framework import (
    AgentSet,
    AgentRole,
    AgentOutput,
    AgentRegistration,
    PosterWorkflow,
    RevisionAction,
    ReviewRecord,
    ReviewDecision,
    VersionDependency,
    KeywordReviewResult,
    KeywordReviewStatus,
    ContentFormat,
    WORKFLOW_REQUEST_FORMAT,
)
from poster_visual_producer.registration import (
    BRIEF_FORMAT,
    SPEC_FORMAT,
    CANDIDATE_FORMAT,
    registration,
    project_budget,
)
from poster_visual_producer.generators import FixtureImageGenerator
from .helpers import fonts, png, ROOT
from examples.visual.workflow_demo import DemoCreativeDirector, DemoLayoutPlanner

QA = ContentFormat(name="poster.sequence-qa", version="1")


@pytest.mark.parametrize(
    "sequence,expected_ops", [("repair_then_layout", 2), ("A_B_A", 2)]
)
def test_consecutive_revisions_preserve_provenance_and_cache(
    tmp_path, sequence, expected_ops
):
    class Layout:
        def __init__(self):
            self.calls = 0

        async def run(self, inputs, context):
            self.calls += 1
            out = await DemoLayoutPlanner("initial").run(inputs, context)
            spec = copy.deepcopy(out.content)
            spec["spec_version"] = self.calls
            if sequence == "repair_then_layout" and self.calls > 1:
                spec["elements"][1]["box"]["y"] = 84
            if sequence == "A_B_A" and self.calls == 2:
                spec["asset_plan"][0]["prompt"] += " Silver reflections."
            return AgentOutput(content=spec, content_format=SPEC_FORMAT)

    class Critic:
        def __init__(self):
            self.calls = 0

        async def run(self, inputs, context):
            self.calls += 1
            v = inputs.content
            action = None
            target = None
            if self.calls <= 2:
                action = (
                    RevisionAction.regenerate_asset
                    if sequence == "repair_then_layout" and self.calls == 1
                    else RevisionAction.revise_layout
                )
                ref = (
                    v["assets"][0]["source_ref"]
                    if action == RevisionAction.regenerate_asset
                    else next(
                        a["reference"]
                        for a in context.memory.context()["artifacts"]
                        if a["name"] == "layout"
                    )
                )
                target = VersionDependency(
                    artifact_id=ref["artifact_id"], revision=ref["revision"]
                )
            checks = tuple(
                KeywordReviewResult(
                    keyword_id=k.keyword_id, status=KeywordReviewStatus.pass_
                )
                for k in context.original_request.keywords
            )
            review = ReviewRecord(
                candidate_id=v["candidate_id"],
                candidate_revision=v["candidate_ref"]["revision"],
                decision=ReviewDecision.fail if action else ReviewDecision.pass_,
                action=action,
                target=target,
                keyword_results=checks,
                details={
                    "issue_category": "asset_artifact"
                    if action == RevisionAction.regenerate_asset
                    else "title_position",
                    "repair_plan": {
                        "expected_versions": v["based_on"],
                        "constraints": ["Single continuous horizon"],
                    },
                }
                if action
                else {},
            )
            return AgentOutput(content={}, content_format=QA, review=review)

    layout, critic = Layout(), Critic()
    count = [0]

    def source(request):
        count[0] += 1
        return png("teal" if count[0] == 2 else "blue")

    generator = FixtureImageGenerator(source)
    regs = [
        AgentRegistration(
            role=AgentRole.creative_director,
            instance_factory=lambda c: DemoCreativeDirector(),
            input_formats=frozenset({WORKFLOW_REQUEST_FORMAT}),
            output_formats=frozenset({BRIEF_FORMAT}),
        ),
        AgentRegistration(
            role=AgentRole.layout_planner,
            instance_factory=lambda c: layout,
            input_formats=frozenset({BRIEF_FORMAT}),
            output_formats=frozenset({SPEC_FORMAT}),
        ),
        registration(generator, fonts()),
        AgentRegistration(
            role=AgentRole.quality_critic,
            instance_factory=lambda c: critic,
            input_formats=frozenset({CANDIDATE_FORMAT}),
            output_formats=frozenset({QA}),
        ),
    ]
    result = asyncio.run(
        PosterWorkflow(AgentSet(regs), root=tmp_path, budget=project_budget()).run(
            ["海洋", "宁静", "蓝色"]
        )
    )
    assert result.status.value == "passed" and result.revision_rounds == 2
    assert result.usage.image_operations == expected_ops
    if sequence == "A_B_A":
        final = Image.open(tmp_path / result.candidate.uri.removeprefix("runs/"))
        assert final.getpixel((50, 300)) == (0, 0, 255)
        assert result.visual.content["assets"][0]["asset_version"] == 3

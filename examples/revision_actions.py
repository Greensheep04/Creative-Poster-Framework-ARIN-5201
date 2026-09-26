from __future__ import annotations

import argparse
import asyncio
import io
import json
from pathlib import Path

from PIL import Image

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
    ExecutionMode,
    KeywordReviewResult,
    KeywordReviewStatus,
    PosterWorkflow,
    ReviewDecision,
    ReviewRecord,
    RevisionAction,
    RunBudget,
    VersionDependency,
    WORKFLOW_REQUEST_FORMAT,
)


BRIEF_FORMAT = ContentFormat(name="poster.brief", version="1")
LAYOUT_FORMAT = ContentFormat(name="poster.layout", version="1")
VISUAL_FORMAT = ContentFormat(name="poster.visual", version="1")
REVIEW_FORMAT = ContentFormat(name="poster.review", version="1")


def stored_reference(context: AgentContext, name: str) -> dict[str, object]:
    artifacts = context.memory.context()["artifacts"]
    stored = next(item for item in artifacts if item["name"] == name)
    return stored["reference"]


def validate_brief(content: object) -> None:
    if not isinstance(content, dict):
        raise ValueError("brief must be a JSON object")
    if not isinstance(content.get("title"), str) or not content["title"].strip():
        raise ValueError("brief title must be non-empty")
    keywords = content.get("keyword_texts")
    if not isinstance(keywords, list) or any(not isinstance(item, str) for item in keywords):
        raise ValueError("brief keyword_texts must be a list of strings")


def validate_layout(content: object) -> None:
    if not isinstance(content, dict):
        raise ValueError("layout must be a JSON object")
    if not isinstance(content.get("title"), str) or not content["title"].strip():
        raise ValueError("layout title must be non-empty")
    columns = content.get("columns")
    if isinstance(columns, bool) or not isinstance(columns, int) or columns < 1:
        raise ValueError("layout columns must be a positive integer")


def validate_visual(content: object) -> None:
    if not isinstance(content, dict):
        raise ValueError("visual result must be a JSON object")
    if not isinstance(content.get("title"), str) or not content["title"].strip():
        raise ValueError("visual title must be non-empty")
    columns = content.get("columns")
    if isinstance(columns, bool) or not isinstance(columns, int) or columns < 1:
        raise ValueError("visual columns must be a positive integer")


class CreativeDirectorAgent(Agent):
    def __init__(self, state: dict[str, list[object]]) -> None:
        self._state = state

    async def run(self, inputs: AgentInput, context: AgentContext) -> AgentOutput:
        if inputs.content_format != WORKFLOW_REQUEST_FORMAT:
            raise ValueError("creative director requires the original request")
        request = context.original_request
        action = context.metadata.revision_action
        if action not in (None, RevisionAction.revise_brief):
            raise ValueError("creative director received an unsupported revision action")
        title = " + ".join(item.text for item in request.keywords)
        if action == RevisionAction.revise_brief:
            title = f"{title} · revised brief"
        content = {
            "title": title,
            "keyword_texts": [item.text for item in request.keywords],
        }
        validate_brief(content)
        self._state["brief_outputs"].append(content)
        return AgentOutput(content=content, content_format=BRIEF_FORMAT)


class LayoutPlannerAgent(Agent):
    def __init__(self, state: dict[str, list[object]]) -> None:
        self._state = state

    async def run(self, inputs: AgentInput, context: AgentContext) -> AgentOutput:
        if inputs.content_format != BRIEF_FORMAT:
            raise ValueError("layout planner requires poster.brief@1")
        validate_brief(inputs.content)
        action = context.metadata.revision_action
        if action not in (
            None,
            RevisionAction.revise_brief,
            RevisionAction.revise_layout,
        ):
            raise ValueError("layout planner received an unsupported revision action")

        brief_reference = stored_reference(context, "brief")
        based_on = {
            brief_reference["artifact_id"]: brief_reference["revision"],
        }
        memory_context = context.memory.context()
        current_notes = [
            item for item in memory_context["notes"]
            if item["based_on"] == based_on
        ]
        prior_note_visible = bool(current_notes)
        if current_notes:
            previous_note = current_notes[-1]
            reference = context.memory.note_reference(previous_note["note_id"])
            note = context.memory.read_note(reference)
            read_note_id = note.note_id
            note_content = note.content
        else:
            note_id = f"layout-note-{context.identity.invocation_id}"
            note_content = {
                "layout_rule": "keep the title readable",
                "brief_revision": brief_reference["revision"],
            }
            context.memory.add_note(
                note_id=note_id,
                content=note_content,
                based_on=based_on,
            )
            reference = context.memory.note_reference(note_id)
            note = context.memory.read_note(reference)
            read_note_id = note.note_id
            note_content = note.content
        self._state["layout_note_events"].append(
            {
                "note_id": read_note_id,
                "based_on": dict(based_on),
                "content": note_content,
                "read_note_id": read_note_id,
                "historical_note_content": note_content,
                "prior_note_visible_in_context": prior_note_visible,
            }
        )

        columns = 4 if action == RevisionAction.revise_layout else 3
        content = {
            "title": inputs.content["title"],
            "keyword_texts": inputs.content["keyword_texts"],
            "columns": columns,
        }
        validate_layout(content)
        self._state["layout_outputs"].append(content)
        return AgentOutput(content=content, content_format=LAYOUT_FORMAT)


class VisualProducerAgent(Agent):
    async def run(self, inputs: AgentInput, context: AgentContext) -> AgentOutput:
        if inputs.content_format != LAYOUT_FORMAT:
            raise ValueError("visual producer requires poster.layout@1")
        validate_layout(inputs.content)
        image_buffer = io.BytesIO()
        Image.new("RGB", (720, 1280), (42, 90, 130)).save(image_buffer, format="PNG")
        candidate = context.files.write_bytes(
            context.files.invocation_path("candidate.png"),
            image_buffer.getvalue(),
            purpose=ArtifactPurpose.candidate,
            media_type="image/png",
        )
        content = {
            "title": inputs.content["title"],
            "columns": inputs.content["columns"],
        }
        return AgentOutput(
            content=content,
            content_format=VISUAL_FORMAT,
            artifacts=(candidate,),
        )


class QualityCriticAgent(Agent):
    def __init__(self, action: RevisionAction, state: dict[str, list[object]]) -> None:
        self._action = action
        self._state = state

    async def run(self, inputs: AgentInput, context: AgentContext) -> AgentOutput:
        if inputs.content_format != VISUAL_FORMAT:
            raise ValueError("quality critic requires poster.visual@1")
        validate_visual(inputs.content)
        candidate = stored_reference(context, "candidate")
        prior_revisions = context.memory.context()["revision_history"]
        first_review = not prior_revisions
        action = self._action if first_review else None
        target = None
        if action is not None:
            target_name = "brief" if action == RevisionAction.revise_brief else "layout"
            target_reference = stored_reference(context, target_name)
            target = VersionDependency(
                artifact_id=target_reference["artifact_id"],
                revision=target_reference["revision"],
            )
        review = ReviewRecord(
            candidate_id=candidate["artifact_id"],
            candidate_revision=candidate["revision"],
            decision=ReviewDecision.fail if first_review else ReviewDecision.pass_,
            action=action,
            target=target,
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
            details={
                "instruction": f"Apply {action.value}." if action else "All checks passed.",
            },
        )
        self._state["review_outputs"].append(review.model_dump(mode="json"))
        return AgentOutput(
            content={"checked": True},
            content_format=REVIEW_FORMAT,
            review=review,
        )


def revision_validator(
    before: object,
    after: object,
    action: RevisionAction,
    request: object,
) -> None:
    if action == RevisionAction.revise_brief:
        expected_keywords = [item.text for item in request.keywords]
        if after["brief"]["keyword_texts"] != expected_keywords:
            raise ValueError("brief revision must retain every original keyword")
    if (
        action == RevisionAction.revise_layout
        and before["layout"]["title"] != after["layout"]["title"]
    ):
        raise ValueError("layout revision must preserve the approved title")


def registrations(
    action: RevisionAction,
    state: dict[str, list[object]],
) -> AgentSet:
    return AgentSet(
        [
            AgentRegistration(
                role=AgentRole.creative_director,
                instance_factory=lambda config: CreativeDirectorAgent(state),
                input_formats=frozenset({WORKFLOW_REQUEST_FORMAT}),
                output_formats=frozenset({BRIEF_FORMAT}),
                output_validator=validate_brief,
            ),
            AgentRegistration(
                role=AgentRole.layout_planner,
                instance_factory=lambda config: LayoutPlannerAgent(state),
                input_formats=frozenset({BRIEF_FORMAT}),
                output_formats=frozenset({LAYOUT_FORMAT}),
                input_validator=validate_brief,
                output_validator=validate_layout,
            ),
            AgentRegistration(
                role=AgentRole.visual_producer,
                instance_factory=lambda config: VisualProducerAgent(),
                input_formats=frozenset({LAYOUT_FORMAT}),
                output_formats=frozenset({VISUAL_FORMAT}),
                input_validator=validate_layout,
                output_validator=validate_visual,
            ),
            AgentRegistration(
                role=AgentRole.quality_critic,
                instance_factory=lambda config: QualityCriticAgent(action, state),
                input_formats=frozenset({VISUAL_FORMAT}),
                output_formats=frozenset({REVIEW_FORMAT}),
                input_validator=validate_visual,
            ),
        ]
    )


async def run_example(root: Path, action_name: str) -> dict[str, object]:
    action = RevisionAction(action_name)
    if action not in {RevisionAction.revise_brief, RevisionAction.revise_layout}:
        raise ValueError("example action must be revise_brief or revise_layout")
    state: dict[str, list[object]] = {
        "brief_outputs": [],
        "layout_outputs": [],
        "layout_note_events": [],
        "review_outputs": [],
    }
    workflow = PosterWorkflow(
        registrations(action, state),
        root=root,
        budget=RunBudget(max_revision_rounds=2),
        revision_validator=revision_validator,
    )
    result = await workflow.run(
        ["green tea", "caffeine-free"],
        constraints={"tone": "calm"},
    )
    if result.status.value != "passed" or result.revision_rounds != 1:
        raise AssertionError("revision example did not complete one reviewed revision")
    if result.candidate is None or result.review is None:
        raise AssertionError("revision example did not publish and review a candidate")
    if (
        result.review.candidate_id != result.candidate.artifact_id
        or result.review.candidate_revision != result.candidate.revision
    ):
        raise AssertionError("final review does not identify the current candidate")
    if len(state["layout_note_events"]) != 2:
        raise AssertionError("layout planner did not demonstrate note reuse or version refresh")
    if action == RevisionAction.revise_brief:
        if state["brief_outputs"][0] == state["brief_outputs"][-1]:
            raise AssertionError("creative director did not revise the brief")
        if state["layout_note_events"][1]["prior_note_visible_in_context"]:
            raise AssertionError("brief revision must invalidate notes based on the prior brief")
    elif state["layout_outputs"][0] == state["layout_outputs"][-1]:
        raise AssertionError("layout planner did not revise the layout")
    return {
        "run_id": result.run_id,
        "execution_mode": ExecutionMode.real.value,
        "revision_action": action.value,
        "status": result.status.value,
        "revision_rounds": result.revision_rounds,
        "call_sequence": [item.value for item in result.call_sequence],
        "initial_brief": state["brief_outputs"][0],
        "final_brief": state["brief_outputs"][-1],
        "initial_layout": state["layout_outputs"][0],
        "final_layout": state["layout_outputs"][-1],
        "layout_note_events": state["layout_note_events"],
        "review_outputs": state["review_outputs"],
        "candidate": result.candidate.model_dump(mode="json"),
        "review": result.review.model_dump(mode="json"),
        "record_path": str(result.record_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(".scratch/revision-actions"))
    parser.add_argument(
        "--action",
        choices=(RevisionAction.revise_brief.value, RevisionAction.revise_layout.value),
        required=True,
    )
    arguments = parser.parse_args()
    result = asyncio.run(run_example(arguments.root, arguments.action))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()

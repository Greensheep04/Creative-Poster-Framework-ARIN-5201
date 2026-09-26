from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO
from pathlib import PurePosixPath

from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError
import pytest

from poster_framework import (
    AgentOutput,
    AgentRegistration,
    AgentRole,
    AgentSet,
    ArtifactPurpose,
    ContentFormat,
    ExecutionMode,
    KeywordReviewResult,
    KeywordReviewStatus,
    ReviewDecision,
    ReviewRecord,
    RunRecord,
)
import poster_framework as framework


BRIEF_FORMAT = ContentFormat(name="test.brief", version="1")
LAYOUT_FORMAT = ContentFormat(name="test.layout", version="1")
VISUAL_FORMAT = ContentFormat(name="test.visual", version="1")
REVIEW_FORMAT = ContentFormat(name="test.review", version="1")


class WorkflowTrace:
    def __init__(self):
        self.roles = []
        self.identities = []
        self.received_keyword_ids = []
        self.review_script = []
        self.review_index = 0
        self.capture_revision_contexts = False
        self.revision_contexts = []
        self.critic_private_contexts = []
        self.reviewed_candidates = []
        self.candidate_references = []
        self.asset_references = []
        self.include_asset = False
        self.include_second_asset = False
        self.change_revised_layout = False
        self.change_protected_business_content = False
        self.change_layout_fingerprint = False
        self.reuse_layout_assets = False
        self.omit_initial_asset_fingerprint = False
        self.omit_layout_assets = False
        self.create_unselected_asset = False
        self.reintroduce_unselected_asset = False
        self.register_unselected_asset = False
        self.add_unregistered_layout_asset = False
        self.wrong_layout_replacement_target = False
        self.duplicate_review_keyword_ids = False
        self.invalid_review_action = None
        self.rename_regenerated_asset = False
        self.return_previous_candidate_on_rerender = False
        self.unselected_asset_reference = None
        self.new_layout_asset_reference = None
        self.layout_contents = []
        self.corrupt_candidate = False
        self.truncate_candidate_data = False
        self.record_model_calls = False
        self.interface_version_mismatch = False
        self.format_path_mismatch = False
        self.omit_last_keyword = False
        self.candidate_size = (720, 1280)
        self.candidate_format = "PNG"
        self.candidate_suffix = ".png"
        self.candidate_media_type = "image/png"


class DeterministicPosterAgent:
    def __init__(self, role, trace):
        self.role = role
        self.trace = trace

    async def run(self, inputs, context):
        self.trace.roles.append(context.identity.role)
        self.trace.identities.append(context.identity)
        if (
            self.trace.capture_revision_contexts
            and context.metadata.revision_action is not None
            and self.role
            == {
                framework.RevisionAction.revise_brief: AgentRole.creative_director,
                framework.RevisionAction.revise_layout: AgentRole.layout_planner,
                framework.RevisionAction.regenerate_asset: AgentRole.visual_producer,
                framework.RevisionAction.rerender: AgentRole.visual_producer,
            }[context.metadata.revision_action]
        ):
            memory_context = context.memory.context()
            model_request = context.build_model_request(
                [{"role": "user", "content": "continue revision"}],
                max_history_chars=128,
            )
            self.trace.revision_contexts.append(
                {
                    "role": context.identity.role,
                    "action": context.metadata.revision_action.value,
                    "target": context.metadata.revision_target.model_dump(mode="json"),
                    "memory": memory_context,
                    "model_request": model_request,
                }
            )
        if self.trace.record_model_calls and self.role in {
            AgentRole.visual_producer,
            AgentRole.quality_critic,
        }:
            await context.call_model(
                provider="deterministic",
                model="workflow-test",
                request_data={"role": self.role.value},
                parameters={},
                invoke=lambda provider, model, request, parameters: "recorded",
                kind=(
                    "image_operation"
                    if self.role == AgentRole.visual_producer
                    else "critic_vision"
                ),
            )
        if self.role == AgentRole.creative_director:
            self.trace.received_keyword_ids.extend(
                item["keyword_id"] for item in inputs.content["keywords"]
            )
            revised = context.metadata.revision_action == framework.RevisionAction.revise_brief
            return AgentOutput(
                content={
                    "headline": "Green tea revised" if revised else "Green tea",
                    "copy": "Brew slowly",
                },
                content_format=BRIEF_FORMAT,
            )
        if self.role == AgentRole.layout_planner:
            revised = context.metadata.revision_action == framework.RevisionAction.revise_layout
            content = {
                "headline": inputs.content["headline"],
                "columns": 4 if revised and self.trace.change_revised_layout else 3,
            }
            if revised and self.trace.change_protected_business_content:
                content["headline"] = "Green tea business copy changed"
            self.trace.layout_contents.append(content)
            return AgentOutput(
                content=content,
                content_format=LAYOUT_FORMAT,
            )
        if self.role == AgentRole.visual_producer:
            image_buffer = BytesIO()
            if self.trace.corrupt_candidate:
                image_buffer.write(b"invalid png")
            else:
                Image.new("RGB", self.trace.candidate_size, (42, 90, 130)).save(
                    image_buffer, format=self.trace.candidate_format
                )
            candidate_data = image_buffer.getvalue()
            if self.trace.truncate_candidate_data:
                candidate_data = candidate_data[: len(candidate_data) // 2]
            if (
                self.trace.return_previous_candidate_on_rerender
                and context.metadata.revision_action == framework.RevisionAction.rerender
            ):
                candidate = self.trace.candidate_references[0]
            else:
                candidate = context.files.write_bytes(
                    context.files.invocation_path(f"candidate{self.trace.candidate_suffix}"),
                    candidate_data,
                    purpose=ArtifactPurpose.candidate,
                    media_type=self.trace.candidate_media_type,
                )
            self.trace.candidate_references.append(candidate)
            artifacts = [candidate]
            generation_action = context.metadata.revision_action
            if self.trace.include_asset and generation_action != framework.RevisionAction.rerender:
                memory_artifacts = context.memory.context()["artifacts"]
                prior_assets = [
                    item for item in memory_artifacts if item["name"].startswith("asset:")
                ]
                if generation_action == framework.RevisionAction.regenerate_asset:
                    target = context.metadata.revision_target
                    target_reference = next(
                        framework.ArtifactReference.model_validate(item["reference"])
                        for item in prior_assets
                        if item["reference"]["artifact_id"] == target.artifact_id
                        and item["reference"]["revision"] == target.revision
                    )
                    filename = (
                        "regenerated/brand.png"
                        if self.trace.rename_regenerated_asset
                        else "hero.png"
                    )
                    asset_buffer = BytesIO()
                    Image.new("RGB", (320, 320), (55, 135, 85)).save(
                        asset_buffer, format="PNG"
                    )
                    asset = context.files.write_bytes(
                        context.files.invocation_path(filename),
                        asset_buffer.getvalue(),
                        purpose=ArtifactPurpose.asset,
                        media_type="image/png",
                        generation_fingerprint=asset_fingerprint("hero:regenerated"),
                        replaces=target_reference,
                    )
                    self.trace.asset_references.append(asset)
                    artifacts.append(asset)
                elif generation_action == framework.RevisionAction.revise_layout:
                    if not self.trace.omit_layout_assets:
                        for item in prior_assets:
                            prior_reference = framework.ArtifactReference.model_validate(
                                item["reference"]
                            )
                            asset_name = PurePosixPath(item["reference"]["uri"]).name
                            prompt = "hero" if asset_name == "hero.png" else "brand"
                            if self.trace.change_layout_fingerprint:
                                prompt = f"{prompt}:changed-layout"
                            fingerprint = asset_fingerprint(prompt)
                            if self.trace.reuse_layout_assets:
                                context.files.register_generation(prior_reference, fingerprint)
                                artifacts.append(prior_reference)
                                continue
                            asset_buffer = BytesIO()
                            color = (25, 110, 70) if prompt.startswith("hero") else (180, 70, 40)
                            Image.new("RGB", (320, 320), color).save(
                                asset_buffer, format="PNG"
                            )
                            replacement_target = (
                                self.trace.unselected_asset_reference
                                if self.trace.wrong_layout_replacement_target
                                else prior_reference
                            )
                            asset = context.files.write_bytes(
                                context.files.invocation_path(asset_name),
                                asset_buffer.getvalue(),
                                purpose=ArtifactPurpose.asset,
                                media_type="image/png",
                                generation_fingerprint=fingerprint,
                                replaces=replacement_target,
                            )
                            self.trace.asset_references.append(asset)
                            artifacts.append(asset)
                        if self.trace.reintroduce_unselected_asset:
                            if self.trace.register_unselected_asset:
                                context.files.register_generation(
                                    self.trace.unselected_asset_reference,
                                    asset_fingerprint("registered historical asset"),
                                )
                            artifacts.append(self.trace.unselected_asset_reference)
                        if self.trace.add_unregistered_layout_asset:
                            added_buffer = BytesIO()
                            Image.new("RGB", (320, 320), (90, 110, 70)).save(
                                added_buffer, format="PNG"
                            )
                            self.trace.new_layout_asset_reference = context.files.write_bytes(
                                context.files.invocation_path("added-layout-asset.png"),
                                added_buffer.getvalue(),
                                purpose=ArtifactPurpose.asset,
                                media_type="image/png",
                            )
                            artifacts.append(self.trace.new_layout_asset_reference)
                else:
                    filenames = ["hero.png"]
                    if self.trace.include_second_asset:
                        filenames.append("brand.png")
                    for filename in filenames:
                        asset_buffer = BytesIO()
                        color = (25, 110, 70) if filename == "hero.png" else (180, 70, 40)
                        Image.new("RGB", (320, 320), color).save(
                            asset_buffer, format="PNG"
                        )
                        fingerprint = (
                            None
                            if self.trace.omit_initial_asset_fingerprint
                            else asset_fingerprint(
                                "hero" if filename == "hero.png" else "brand"
                            )
                        )
                        asset = context.files.write_bytes(
                            context.files.invocation_path(filename),
                            asset_buffer.getvalue(),
                            purpose=ArtifactPurpose.asset,
                            media_type="image/png",
                            generation_fingerprint=fingerprint,
                        )
                        self.trace.asset_references.append(asset)
                        artifacts.append(asset)
                    if self.trace.create_unselected_asset:
                        hidden_buffer = BytesIO()
                        Image.new("RGB", (320, 320), (80, 90, 100)).save(
                            hidden_buffer, format="PNG"
                        )
                        self.trace.unselected_asset_reference = context.files.write_bytes(
                            context.files.invocation_path("unselected.png"),
                            hidden_buffer.getvalue(),
                            purpose=ArtifactPurpose.asset,
                            media_type="image/png",
                        )
            return AgentOutput(
                content={"render": "completed"},
                content_format=VISUAL_FORMAT,
                artifacts=tuple(artifacts),
            )

        memory_artifacts = context.memory.context()["artifacts"]
        candidate = next(
            item
            for item in memory_artifacts
            if item["name"] == "candidate"
        )["reference"]
        self.trace.reviewed_candidates.append(candidate)
        if self.trace.capture_revision_contexts:
            review_index = self.trace.review_index
            context.memory.append_private_message(
                {"content": f"critic-private-message-{review_index}"}
            )
            context.memory.add_note(
                note_id=f"critic-private-note-{review_index}",
                content={"content": f"critic-private-note-{review_index}"},
                based_on={},
            )
            self.trace.critic_private_contexts.append(context.memory.context())
        plan = (
            self.trace.review_script[self.trace.review_index]
            if self.trace.review_index < len(self.trace.review_script)
            else {}
        )
        self.trace.review_index += 1
        if plan.get("stale_candidate"):
            candidate = self.trace.candidate_references[0].model_dump(mode="json")
        action = plan.get("action")
        if self.trace.invalid_review_action is not None:
            action = self.trace.invalid_review_action
        target = None
        if action is not None:
            target_name = {
                "revise_brief": "brief",
                "revise_layout": "layout",
                "rerender": "candidate",
            }.get(action)
            target_reference = next(
                item["reference"]
                for item in memory_artifacts
                if (
                    item["name"] == target_name
                    if target_name is not None
                    else item["name"].startswith("asset:")
                )
            )
            target = {
                "artifact_id": target_reference["artifact_id"],
                "revision": target_reference["revision"],
            }
            if plan.get("stale_target"):
                target["revision"] = "stale-revision"
        return AgentOutput(
            content={"checked": True},
            content_format=REVIEW_FORMAT,
            review=ReviewRecord(
                candidate_id=candidate["artifact_id"],
                candidate_revision=candidate["revision"],
                decision=ReviewDecision(plan.get("decision", "pass")),
                action=framework.RevisionAction(action) if action is not None else None,
                target=target,
                keyword_results=tuple(
                    KeywordReviewResult(
                        keyword_id=(
                            inputs.metadata.original_request.keywords[0].keyword_id
                            if self.trace.duplicate_review_keyword_ids
                            else item.keyword_id
                        ),
                        status=KeywordReviewStatus(
                            plan.get("keyword_status", "pass")
                            if index == 0
                            else "pass"
                        ),
                    )
                    for index, item in enumerate(
                        inputs.metadata.original_request.keywords[
                            :-1 if self.trace.omit_last_keyword else None
                        ]
                    )
                ),
                details={"issue": plan.get("issue", "copy needs review")},
            ),
        )


def make_agent_set(trace):
    formats = {
        AgentRole.creative_director: (framework.WORKFLOW_REQUEST_FORMAT, BRIEF_FORMAT),
        AgentRole.layout_planner: (BRIEF_FORMAT, LAYOUT_FORMAT),
        AgentRole.visual_producer: (LAYOUT_FORMAT, VISUAL_FORMAT),
        AgentRole.quality_critic: (VISUAL_FORMAT, REVIEW_FORMAT),
    }
    if trace.format_path_mismatch:
        formats[AgentRole.layout_planner] = (
            BRIEF_FORMAT,
            ContentFormat(name="test.unreachable", version="1"),
        )
    return AgentSet(
        AgentRegistration(
            role=role,
            instance_factory=lambda config, current_role=role: DeterministicPosterAgent(
                current_role, trace
            ),
            input_formats=frozenset({input_format}),
            output_formats=frozenset({output_format}),
            interface_versions=(
                frozenset({"unsupported"})
                if trace.interface_version_mismatch and role == AgentRole.quality_critic
                else frozenset({framework.INTERFACE_VERSION})
            ),
        )
        for role, (input_format, output_format) in formats.items()
    )


def asset_fingerprint(prompt):
    return framework.GenerationFingerprint.build(
        provider="pillow",
        model="poster-renderer-test",
        weights={"renderer": "deterministic"},
        prompt=prompt,
        parameters={"size": [320, 320]},
        reference_materials=(),
        processing_config={"format": "png", "mode": "RGB"},
    )


def assert_failed_run_record(root, error_type):
    record_paths = list((root / "runs").glob("*/run-record.json"))
    assert len(record_paths) == 1
    record = RunRecord.model_validate_json(record_paths[0].read_text(encoding="utf-8"))
    assert record.final_result == {"status": "failed", "error_type": error_type}
    return record


def test_invocation_path_scopes_files_and_rejects_directory_escape(tmp_path):
    store = framework.RunFileStore(tmp_path, "run-path")
    first_identity = framework.ExecutionIdentity(
        run_id="run-path",
        role=AgentRole.visual_producer,
        invocation_id="invoke-1",
        execution_mode=ExecutionMode.fake,
    )
    second_identity = first_identity.model_copy(update={"invocation_id": "invoke-2"})

    first_path = store.scope(first_identity).invocation_path("images/candidate.png")
    second_path = store.scope(second_identity).invocation_path("images/candidate.png")

    assert first_path == "invocations/visual_producer/invoke-1/images/candidate.png"
    assert second_path == "invocations/visual_producer/invoke-2/images/candidate.png"
    with pytest.raises(framework.FileSecurityError):
        store.scope(first_identity).invocation_path("../candidate.png")


def test_poster_workflow_executes_langgraph_and_persists_a_current_review(tmp_path):
    trace = WorkflowTrace()
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    result = asyncio.run(
        workflow.run(
            ["tea", "caffeine-free"],
            constraints={"tone": "calm"},
            execution_mode=ExecutionMode.fake,
        )
    )

    assert result.status == framework.WorkflowStatus.passed
    assert result.run_id.startswith("run-")
    assert trace.roles == [
        AgentRole.creative_director,
        AgentRole.layout_planner,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
    ]
    assert len({identity.invocation_id for identity in trace.identities}) == 4
    assert trace.received_keyword_ids == [
        item.keyword_id for item in result.original_request.keywords
    ]
    assert result.review.candidate_id == result.candidate.artifact_id
    assert result.review.candidate_revision == result.candidate.revision
    assert {item.keyword_id for item in result.review.keyword_results} == {
        item.keyword_id for item in result.original_request.keywords
    }

    record = RunRecord.model_validate_json(result.record_path.read_text(encoding="utf-8"))
    assert record.run_id == result.run_id
    assert record.original_request == result.original_request
    assert record.final_result["status"] == "passed"
    assert len(record.artifacts) == 5

    session = framework.RunSession.load(result.record_path, root=tmp_path)
    image_data = session.file_store.read(result.candidate)
    with Image.open(BytesIO(image_data)) as image:
        image.verify()
    with Image.open(BytesIO(image_data)) as image:
        assert image.format == "PNG"
        assert image.size == (720, 1280)


@pytest.mark.parametrize(
    ("action", "expected_roles"),
    [
        (
            framework.RevisionAction.revise_brief,
            [
                AgentRole.creative_director,
                AgentRole.layout_planner,
                AgentRole.visual_producer,
                AgentRole.quality_critic,
            ]
            * 2,
        ),
        (
            framework.RevisionAction.revise_layout,
            [
                AgentRole.creative_director,
                AgentRole.layout_planner,
                AgentRole.visual_producer,
                AgentRole.quality_critic,
                AgentRole.layout_planner,
                AgentRole.visual_producer,
                AgentRole.quality_critic,
            ],
        ),
        (
            framework.RevisionAction.regenerate_asset,
            [
                AgentRole.creative_director,
                AgentRole.layout_planner,
                AgentRole.visual_producer,
                AgentRole.quality_critic,
                AgentRole.visual_producer,
                AgentRole.quality_critic,
            ],
        ),
        (
            framework.RevisionAction.rerender,
            [
                AgentRole.creative_director,
                AgentRole.layout_planner,
                AgentRole.visual_producer,
                AgentRole.quality_critic,
                AgentRole.visual_producer,
                AgentRole.quality_critic,
            ],
        ),
    ],
)
def test_each_revision_action_runs_the_declared_langgraph_route(
    tmp_path, action, expected_roles
):
    trace = WorkflowTrace()
    trace.include_asset = action in {
        framework.RevisionAction.regenerate_asset,
        framework.RevisionAction.rerender,
        framework.RevisionAction.revise_layout,
    }
    trace.change_revised_layout = action == framework.RevisionAction.revise_layout
    trace.review_script = [{"decision": "fail", "action": action.value}]
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    result = asyncio.run(
        workflow.run(
            ["tea", "caffeine-free"],
            execution_mode=ExecutionMode.fake,
        )
    )

    assert result.status == framework.WorkflowStatus.passed
    assert trace.roles == expected_roles
    assert result.revision_rounds == 1
    assert trace.candidate_references[-1].artifact_id == result.candidate.artifact_id
    assert result.review.candidate_id == result.candidate.artifact_id
    assert result.review.candidate_revision == result.candidate.revision
    if action == framework.RevisionAction.revise_layout:
        assert trace.layout_contents[0] != trace.layout_contents[1]
        assert len(result.assets) == 1
        assert result.assets[0].artifact_id == trace.asset_references[0].artifact_id
        assert result.assets[0].sha256 == trace.asset_references[0].sha256
    if action == framework.RevisionAction.regenerate_asset:
        assert len(result.assets) == 1
        assert result.assets[0].artifact_id == trace.asset_references[-1].artifact_id
    if action == framework.RevisionAction.rerender:
        assert len(result.assets) == 1
        assert result.assets[0].artifact_id == trace.asset_references[0].artifact_id


@pytest.mark.parametrize(
    "action, receiving_role",
    [
        (framework.RevisionAction.revise_brief, AgentRole.creative_director),
        (framework.RevisionAction.revise_layout, AgentRole.layout_planner),
        (framework.RevisionAction.regenerate_asset, AgentRole.visual_producer),
        (framework.RevisionAction.rerender, AgentRole.visual_producer),
    ],
)
def test_review_revision_instructions_reach_routed_agent_without_critic_private_context(
    tmp_path, action, receiving_role
):
    trace = WorkflowTrace()
    trace.capture_revision_contexts = True
    trace.include_asset = action in {
        framework.RevisionAction.regenerate_asset,
        framework.RevisionAction.rerender,
    }
    trace.review_script = [
        {
            "decision": "fail",
            "action": action.value,
            "issue": f"{action.value}-issue-{index}",
        }
        for index in range(3)
    ]
    workflow = framework.PosterWorkflow(
        make_agent_set(trace),
        root=tmp_path,
        budget=framework.RunBudget(max_revision_rounds=3),
    )

    result = asyncio.run(
        workflow.run(
            ["tea", "caffeine-free"],
            constraints={"tone": "calm"},
            execution_mode=ExecutionMode.fake,
        )
    )

    assert result.status == framework.WorkflowStatus.passed
    assert result.revision_rounds == 3
    assert len(trace.revision_contexts) == 3
    critic_identities = [
        identity
        for identity in trace.identities
        if identity.role == AgentRole.quality_critic
    ]
    assert len(trace.critic_private_contexts) == 4
    assert len(trace.reviewed_candidates) == 4

    for index, observed in enumerate(trace.revision_contexts):
        assert observed["role"] == receiving_role
        assert observed["action"] == action.value
        memory_context = observed["memory"]
        model_context = observed["model_request"]["context"]
        original_request = memory_context["original_request"]
        assert [item["text"] for item in original_request["keywords"]] == [
            "tea",
            "caffeine-free",
        ]
        assert original_request["constraints"] == [{"name": "tone", "value": "calm"}]
        assert observed["model_request"]["original_request"] == original_request
        assert model_context["original_request"] == original_request
        assert memory_context["private_messages"] == []
        assert memory_context["notes"] == []
        assert model_context["private_messages"] == []
        assert model_context["notes"] == []
        assert not any(
            item["name"] == "quality_review" for item in memory_context["artifacts"]
        )

        history = memory_context["revision_history"]
        assert model_context["revision_history"] == history
        assert len(history) == min(index + 1, 2)
        latest_revision = history[-1]
        changes = latest_revision["changes"]
        assert latest_revision["role"] is None
        assert changes["action"] == action.value
        assert changes["details"] == {"issue": f"{action.value}-issue-{index}"}
        assert changes["target"] == observed["target"]
        assert changes["reviewer_identity"] == critic_identities[index].model_dump(
            mode="json"
        )
        assert [
            item["changes"]["details"]["issue"] for item in history
        ] == [
            f"{action.value}-issue-{revision_index}"
            for revision_index in range(max(0, index - 1), index + 1)
        ]

        critic_context = trace.critic_private_contexts[index]
        assert critic_context["private_messages"] == [
            {"content": f"critic-private-message-{index}"}
        ]
        assert {"content": f"critic-private-note-{index}"} in [
            item["content"] for item in critic_context["notes"]
        ]

    assert result.review.candidate_id == result.candidate.artifact_id
    assert result.review.candidate_revision == result.candidate.revision
    assert result.review.reviewer_identity == critic_identities[-1]
    assert trace.reviewed_candidates[-1]["artifact_id"] == result.candidate.artifact_id


def test_asset_regeneration_uses_the_review_target_and_preserves_other_assets(tmp_path):
    trace = WorkflowTrace()
    trace.include_asset = True
    trace.include_second_asset = True
    trace.rename_regenerated_asset = True
    trace.review_script = [{"decision": "fail", "action": "regenerate_asset"}]
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    result = asyncio.run(
        workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
    )

    original_hero, original_brand = trace.asset_references[:2]
    assert result.status == framework.WorkflowStatus.passed
    assert len(result.assets) == 2
    replacement = next(item for item in result.assets if item.artifact_id != original_brand.artifact_id)
    retained = next(item for item in result.assets if item.artifact_id == original_brand.artifact_id)
    assert replacement.artifact_id == trace.asset_references[-1].artifact_id
    assert replacement.uri.endswith("regenerated/brand.png")
    assert retained == original_brand
    assert retained.sha256 == original_brand.sha256
    assert replacement.artifact_id != original_hero.artifact_id
    record = RunRecord.model_validate_json(result.record_path.read_text(encoding="utf-8"))
    replacement_record = next(
        item for item in record.generation_records if item.reference == replacement
    )
    assert replacement_record.replaces == framework.VersionDependency(
        artifact_id=original_hero.artifact_id,
        revision=original_hero.revision,
    )
    loaded = framework.RunSession.load(result.record_path, root=tmp_path)
    assert loaded.file_store.generation_record(replacement) == replacement_record


def test_layout_change_uses_new_asset_when_generation_fingerprint_changes(tmp_path):
    trace = WorkflowTrace()
    trace.include_asset = True
    trace.change_revised_layout = True
    trace.change_layout_fingerprint = True
    trace.review_script = [{"decision": "fail", "action": "revise_layout"}]
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    result = asyncio.run(
        workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
    )

    assert result.status == framework.WorkflowStatus.passed
    assert result.layout.content != trace.layout_contents[0]
    assert result.assets[0].artifact_id == trace.asset_references[-1].artifact_id
    assert result.assets[0].artifact_id != trace.asset_references[0].artifact_id


def test_layout_revision_registers_and_reuses_unchanged_asset_reference(tmp_path):
    trace = WorkflowTrace()
    trace.include_asset = True
    trace.change_revised_layout = True
    trace.reuse_layout_assets = True
    trace.review_script = [{"decision": "fail", "action": "revise_layout"}]
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    result = asyncio.run(
        workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
    )

    assert result.status == framework.WorkflowStatus.passed
    assert result.layout.content != trace.layout_contents[0]
    assert len(trace.asset_references) == 1
    assert result.assets == (trace.asset_references[0],)


def test_layout_revision_accepts_a_new_asset_from_the_current_invocation(tmp_path):
    trace = WorkflowTrace()
    trace.include_asset = True
    trace.reuse_layout_assets = True
    trace.add_unregistered_layout_asset = True
    trace.review_script = [{"decision": "fail", "action": "revise_layout"}]
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    result = asyncio.run(
        workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
    )

    current_invocation = next(
        identity
        for identity in reversed(trace.identities)
        if identity.role == AgentRole.visual_producer
    )
    assert result.status == framework.WorkflowStatus.passed
    assert result.assets == (
        trace.asset_references[0],
        trace.new_layout_asset_reference,
    )
    assert trace.new_layout_asset_reference.source_id == current_invocation.invocation_id
    loaded = framework.RunSession.load(result.record_path, root=tmp_path)
    assert loaded.file_store.read(trace.new_layout_asset_reference)
    assert loaded.file_store.generation_record(trace.new_layout_asset_reference) is None


def test_layout_revision_accepts_a_historical_asset_registered_and_verified_this_invocation(
    tmp_path,
):
    trace = WorkflowTrace()
    trace.include_asset = True
    trace.create_unselected_asset = True
    trace.reuse_layout_assets = True
    trace.reintroduce_unselected_asset = True
    trace.register_unselected_asset = True
    trace.review_script = [{"decision": "fail", "action": "revise_layout"}]
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    result = asyncio.run(
        workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
    )

    current_invocation = next(
        identity
        for identity in reversed(trace.identities)
        if identity.role == AgentRole.visual_producer
    )
    assert result.status == framework.WorkflowStatus.passed
    assert trace.unselected_asset_reference in result.assets
    assert trace.unselected_asset_reference.source_id != current_invocation.invocation_id
    loaded = framework.RunSession.load(result.record_path, root=tmp_path)
    assert loaded.file_store.read(trace.unselected_asset_reference)
    registration = loaded.file_store.generation_record(
        trace.unselected_asset_reference,
        invocation_id=current_invocation.invocation_id,
    )
    assert registration.fingerprint.key == asset_fingerprint(
        "registered historical asset"
    ).key


@pytest.mark.parametrize(
    ("omit_initial_fingerprint", "register_current_fingerprint", "omit_asset"),
    [
        pytest.param(True, True, False, id="missing-original-fingerprint"),
        pytest.param(True, False, True, id="missing-both-fingerprints"),
        pytest.param(False, False, True, id="missing-current-fingerprint"),
    ],
)
def test_layout_revision_rejects_unproven_asset_reuse(
    tmp_path,
    omit_initial_fingerprint,
    register_current_fingerprint,
    omit_asset,
):
    trace = WorkflowTrace()
    trace.include_asset = True
    trace.omit_initial_asset_fingerprint = omit_initial_fingerprint
    trace.reuse_layout_assets = register_current_fingerprint
    trace.omit_layout_assets = omit_asset
    trace.record_model_calls = True
    trace.review_script = [{"decision": "fail", "action": "revise_layout"}]
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    with pytest.raises(framework.FileSecurityError):
        asyncio.run(
            workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
        )

    assert trace.roles == [
        AgentRole.creative_director,
        AgentRole.layout_planner,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
        AgentRole.layout_planner,
        AgentRole.visual_producer,
    ]
    record = assert_failed_run_record(tmp_path, "FileSecurityError")
    assert [event.identity.role for event in record.model_events] == [
        AgentRole.visual_producer,
        AgentRole.quality_critic,
        AgentRole.visual_producer,
    ]


def test_layout_revision_rejects_an_unselected_historical_asset_without_current_provenance(
    tmp_path,
):
    trace = WorkflowTrace()
    trace.include_asset = True
    trace.create_unselected_asset = True
    trace.reuse_layout_assets = True
    trace.reintroduce_unselected_asset = True
    trace.record_model_calls = True
    trace.review_script = [{"decision": "fail", "action": "revise_layout"}]
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    with pytest.raises(framework.FileSecurityError):
        asyncio.run(
            workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
        )

    assert trace.roles == [
        AgentRole.creative_director,
        AgentRole.layout_planner,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
        AgentRole.layout_planner,
        AgentRole.visual_producer,
    ]
    record = assert_failed_run_record(tmp_path, "FileSecurityError")
    assert [event.identity.role for event in record.model_events] == [
        AgentRole.visual_producer,
        AgentRole.quality_critic,
        AgentRole.visual_producer,
    ]


def test_layout_revision_rejects_a_replacement_for_an_unselected_asset(tmp_path):
    trace = WorkflowTrace()
    trace.include_asset = True
    trace.create_unselected_asset = True
    trace.wrong_layout_replacement_target = True
    trace.record_model_calls = True
    trace.review_script = [{"decision": "fail", "action": "revise_layout"}]
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    with pytest.raises(framework.FileSecurityError):
        asyncio.run(
            workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
        )

    assert trace.roles == [
        AgentRole.creative_director,
        AgentRole.layout_planner,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
        AgentRole.layout_planner,
        AgentRole.visual_producer,
    ]
    record = assert_failed_run_record(tmp_path, "FileSecurityError")
    assert [event.identity.role for event in record.model_events] == [
        AgentRole.visual_producer,
        AgentRole.quality_critic,
        AgentRole.visual_producer,
    ]


def test_real_pillow_rerender_accepts_new_candidate_with_same_bytes_and_no_image_call(tmp_path):
    trace = WorkflowTrace()
    trace.review_script = [{"decision": "fail", "action": "rerender"}]
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    result = asyncio.run(workflow.run(["tea", "caffeine-free"]))

    first, second = trace.candidate_references
    assert result.status == framework.WorkflowStatus.passed
    assert first.artifact_id != second.artifact_id
    assert first.sha256 == second.sha256
    assert result.candidate == second
    assert not any(event.kind == "image_operation" for event in result.model_events)


def test_visual_producer_cannot_return_the_previous_candidate_for_rerender(tmp_path):
    trace = WorkflowTrace()
    trace.return_previous_candidate_on_rerender = True
    trace.review_script = [{"decision": "fail", "action": "rerender"}]
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    with pytest.raises(framework.FileSecurityError):
        asyncio.run(
            workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
        )


def test_revision_budget_above_langgraph_default_runs_to_completion(tmp_path):
    trace = WorkflowTrace()
    trace.review_script = [
        {"decision": "fail", "action": "revise_brief", "issue": f"copy-{index}"}
        for index in range(8)
    ]
    workflow = framework.PosterWorkflow(
        make_agent_set(trace),
        root=tmp_path,
        budget=framework.RunBudget(max_revision_rounds=8),
    )

    result = asyncio.run(
        workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
    )

    assert result.status == framework.WorkflowStatus.passed
    assert result.revision_rounds == 8


@pytest.mark.parametrize(
    ("failure_count", "final_decision", "expected_status"),
    [
        (4, "pass", framework.WorkflowStatus.passed),
        (5, "fail", framework.WorkflowStatus.needs_review),
    ],
)
def test_default_budget_supports_four_revisions_and_stops_before_fifth(
    tmp_path, failure_count, final_decision, expected_status
):
    trace = WorkflowTrace()
    trace.record_model_calls = True
    trace.review_script = [
        {
            "decision": "fail",
            "action": "rerender",
            "issue": f"distinct issue {index}",
        }
        for index in range(failure_count)
    ]
    if final_decision == "pass":
        trace.review_script.append({"decision": "pass"})
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    result = asyncio.run(
        workflow.run(["tea", "caffeine-free"], execution_mode=framework.ExecutionMode.fake)
    )

    expected_roles = [
        AgentRole.creative_director,
        AgentRole.layout_planner,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
    ]
    assert result.status == expected_status
    assert result.revision_rounds == 4
    assert result.call_sequence == tuple(expected_roles)
    assert trace.roles == expected_roles
    assert trace.review_index == 5
    assert len(trace.reviewed_candidates) == 5
    assert len(trace.candidate_references) == 5
    assert result.usage.image_operations == 5
    assert result.usage.critic_vision_calls == 5
    assert result.usage.model_calls == 10
    assert len(result.model_events) == 10
    assert all(event.status == "succeeded" and event.executed for event in result.model_events)
    if final_decision == "fail":
        assert result.reason == "revision round budget is exhausted"
    else:
        assert result.reason is None


def test_explicit_smaller_revision_budget_still_stops_at_its_limit(tmp_path):
    trace = WorkflowTrace()
    trace.record_model_calls = True
    trace.review_script = [
        {
            "decision": "fail",
            "action": "rerender",
            "issue": f"distinct issue {index}",
        }
        for index in range(3)
    ]
    workflow = framework.PosterWorkflow(
        make_agent_set(trace),
        root=tmp_path,
        budget=framework.RunBudget(max_revision_rounds=2),
    )

    result = asyncio.run(
        workflow.run(["tea", "caffeine-free"], execution_mode=framework.ExecutionMode.fake)
    )

    expected_roles = [
        AgentRole.creative_director,
        AgentRole.layout_planner,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
    ]
    assert result.status == framework.WorkflowStatus.needs_review
    assert result.revision_rounds == 2
    assert result.reason == "revision round budget is exhausted"
    assert trace.roles == expected_roles
    assert trace.review_index == 3
    assert result.usage.image_operations == 3
    assert result.usage.critic_vision_calls == 3
    assert result.usage.model_calls == 6
    assert len(result.model_events) == 6
    assert all(event.status == "succeeded" and event.executed for event in result.model_events)


def test_revision_business_validator_receives_before_after_action_and_request(tmp_path):
    trace = WorkflowTrace()
    trace.change_revised_layout = True
    trace.review_script = [{"decision": "fail", "action": "revise_layout"}]
    validation_calls = []

    def validate_revision(before, after, action, original_request):
        validation_calls.append((before, after, action, original_request))
        assert before["brief"] == after["brief"]
        assert before["layout"] != after["layout"]
        assert original_request.run_id == before["run_id"]

    workflow = framework.PosterWorkflow(
        make_agent_set(trace),
        root=tmp_path,
        revision_validator=validate_revision,
    )

    result = asyncio.run(
        workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
    )

    assert result.status == framework.WorkflowStatus.passed
    assert len(validation_calls) == 1
    assert validation_calls[0][2] == framework.RevisionAction.revise_layout


def test_format_preflight_rejects_unsupported_interface_before_agent_calls(tmp_path):
    trace = WorkflowTrace()
    trace.interface_version_mismatch = True
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    with pytest.raises(framework.InterfaceVersionCompatibilityError):
        asyncio.run(
            workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
        )

    assert trace.roles == []
    record_paths = list((tmp_path / "runs").glob("*/run-record.json"))
    assert len(record_paths) == 1
    record = RunRecord.model_validate_json(record_paths[0].read_text(encoding="utf-8"))
    assert record.final_result["status"] == "failed"
    assert record.model_events == ()
    assert record.usage.model_calls == 0


def test_format_preflight_rejects_a_broken_adjacent_path_before_agent_calls(tmp_path):
    trace = WorkflowTrace()
    trace.format_path_mismatch = True
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    with pytest.raises(framework.ContentFormatCompatibilityError):
        asyncio.run(
            workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
        )

    assert trace.roles == []
    record_paths = list((tmp_path / "runs").glob("*/run-record.json"))
    assert len(record_paths) == 1
    record = RunRecord.model_validate_json(record_paths[0].read_text(encoding="utf-8"))
    assert record.final_result["status"] == "failed"
    assert record.model_events == ()
    assert record.usage.model_calls == 0


@pytest.mark.parametrize(
    ("action", "include_asset"),
    [
        ("revise_brief", False),
        ("revise_layout", False),
        ("regenerate_asset", True),
        ("rerender", False),
    ],
)
def test_stale_revision_target_fails_before_routed_agent_runs(
    tmp_path, action, include_asset
):
    trace = WorkflowTrace()
    trace.include_asset = include_asset
    trace.record_model_calls = True
    trace.review_script = [
        {"decision": "fail", "action": action, "stale_target": True}
    ]
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    with pytest.raises(framework.AgentIdentityMismatchError):
        asyncio.run(
            workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
        )

    assert trace.roles == [
        AgentRole.creative_director,
        AgentRole.layout_planner,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
    ]
    record = assert_failed_run_record(tmp_path, "AgentIdentityMismatchError")
    assert [event.identity.role for event in record.model_events] == [
        AgentRole.visual_producer,
        AgentRole.quality_critic,
    ]


def test_invalid_review_action_fails_without_routing_a_follow_up_agent(tmp_path):
    trace = WorkflowTrace()
    trace.include_asset = True
    trace.record_model_calls = True
    trace.invalid_review_action = "replace_everything"
    trace.review_script = [{"decision": "fail", "action": "rerender"}]
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    with pytest.raises(ValueError):
        asyncio.run(
            workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
        )

    assert trace.roles == [
        AgentRole.creative_director,
        AgentRole.layout_planner,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
    ]
    record = assert_failed_run_record(tmp_path, "ValueError")
    assert [event.identity.role for event in record.model_events] == [
        AgentRole.visual_producer,
        AgentRole.quality_critic,
    ]


def test_duplicate_keyword_review_ids_cannot_pass_or_route_a_follow_up_agent(tmp_path):
    trace = WorkflowTrace()
    trace.record_model_calls = True
    trace.duplicate_review_keyword_ids = True
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    with pytest.raises(ValidationError):
        asyncio.run(
            workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
        )

    assert trace.roles == [
        AgentRole.creative_director,
        AgentRole.layout_planner,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
    ]
    record = assert_failed_run_record(tmp_path, "ValidationError")
    assert [event.identity.role for event in record.model_events] == [
        AgentRole.visual_producer,
        AgentRole.quality_critic,
    ]


def test_revision_validator_rejects_protected_business_content_change(tmp_path):
    trace = WorkflowTrace()
    trace.change_protected_business_content = True
    trace.record_model_calls = True
    trace.review_script = [{"decision": "fail", "action": "revise_layout"}]

    def reject_layout_change(before, after, action, original_request):
        assert action == framework.RevisionAction.revise_layout
        assert original_request.run_id == before["run_id"]
        if before["layout"]["headline"] != after["layout"]["headline"]:
            raise ValueError("protected business content changed")

    workflow = framework.PosterWorkflow(
        make_agent_set(trace),
        root=tmp_path,
        revision_validator=reject_layout_change,
    )

    with pytest.raises(ValueError, match="protected business content changed"):
        asyncio.run(
            workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
        )

    assert trace.roles == [
        AgentRole.creative_director,
        AgentRole.layout_planner,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
        AgentRole.layout_planner,
    ]
    record = assert_failed_run_record(tmp_path, "ValueError")
    assert [event.identity.role for event in record.model_events] == [
        AgentRole.visual_producer,
        AgentRole.quality_critic,
    ]


def test_unknown_keyword_review_cannot_pass(tmp_path):
    trace = WorkflowTrace()
    trace.review_script = [{"decision": "pass", "keyword_status": "unknown"}]
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    result = asyncio.run(
        workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
    )

    assert result.status == framework.WorkflowStatus.needs_review


def test_incomplete_keyword_review_cannot_pass(tmp_path):
    trace = WorkflowTrace()
    trace.omit_last_keyword = True
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    result = asyncio.run(
        workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
    )

    assert result.status == framework.WorkflowStatus.needs_review


def test_same_review_issue_after_revision_stops_with_needs_review(tmp_path):
    trace = WorkflowTrace()
    trace.review_script = [
        {"decision": "fail", "action": "revise_brief"},
        {"decision": "fail", "action": "revise_brief"},
    ]
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    result = asyncio.run(
        workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
    )

    assert result.status == framework.WorkflowStatus.needs_review
    assert result.revision_rounds == 1
    assert trace.roles == [
        AgentRole.creative_director,
        AgentRole.layout_planner,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
    ] * 2


def test_revision_budget_stops_before_the_routed_agent(tmp_path):
    trace = WorkflowTrace()
    trace.review_script = [{"decision": "fail", "action": "rerender"}]
    workflow = framework.PosterWorkflow(
        make_agent_set(trace),
        root=tmp_path,
        budget=framework.RunBudget(max_revision_rounds=0),
    )

    result = asyncio.run(
        workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
    )

    assert result.status == framework.WorkflowStatus.needs_review
    assert trace.roles == [
        AgentRole.creative_director,
        AgentRole.layout_planner,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
    ]


def test_image_operation_budget_is_checked_before_the_next_generation(tmp_path):
    trace = WorkflowTrace()
    trace.record_model_calls = True
    trace.review_script = [{"decision": "fail", "action": "rerender"}]
    workflow = framework.PosterWorkflow(
        make_agent_set(trace),
        root=tmp_path,
        budget=framework.RunBudget(max_image_operations=1),
    )

    result = asyncio.run(
        workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
    )

    assert result.status == framework.WorkflowStatus.needs_review
    assert result.usage.image_operations == 1
    denied = [event for event in result.model_events if event.status == "denied"]
    assert len(denied) == 1
    assert denied[0].executed is False


def test_critic_vision_budget_is_checked_before_the_next_review(tmp_path):
    trace = WorkflowTrace()
    trace.record_model_calls = True
    trace.review_script = [{"decision": "fail", "action": "rerender"}]
    workflow = framework.PosterWorkflow(
        make_agent_set(trace),
        root=tmp_path,
        budget=framework.RunBudget(max_critic_vision_calls=1),
    )

    result = asyncio.run(
        workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
    )

    assert result.status == framework.WorkflowStatus.needs_review
    assert result.usage.critic_vision_calls == 1
    denied = [
        event
        for event in result.model_events
        if event.kind == "critic_vision" and event.status == "denied"
    ]
    assert len(denied) == 1
    assert denied[0].executed is False


def test_corrupt_candidate_records_failure_and_propagates_image_error(tmp_path):
    trace = WorkflowTrace()
    trace.corrupt_candidate = True
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    with pytest.raises(UnidentifiedImageError):
        asyncio.run(
            workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
        )

    record_paths = list((tmp_path / "runs").glob("*/run-record.json"))
    assert len(record_paths) == 1
    record = RunRecord.model_validate_json(record_paths[0].read_text(encoding="utf-8"))
    assert record.final_result["status"] == "failed"


def test_truncated_jpeg_fails_before_quality_review_and_records_failure(tmp_path):
    trace = WorkflowTrace()
    trace.candidate_format = "JPEG"
    trace.candidate_suffix = ".jpg"
    trace.candidate_media_type = "image/jpeg"
    trace.truncate_candidate_data = True
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    with pytest.raises(OSError):
        asyncio.run(workflow.run(["tea", "caffeine-free"]))

    assert trace.roles == [
        AgentRole.creative_director,
        AgentRole.layout_planner,
        AgentRole.visual_producer,
    ]
    record = assert_failed_run_record(tmp_path, "OSError")
    record_path = next((tmp_path / "runs").glob("*/run-record.json"))
    session = framework.RunSession.load(record_path, root=tmp_path)
    candidate_data = session.file_store.read(trace.candidate_references[0])
    with Image.open(BytesIO(candidate_data)) as image:
        assert image.format == "JPEG"
        assert image.size == (720, 1280)
    assert record.final_result == {"status": "failed", "error_type": "OSError"}


def test_wrong_candidate_dimensions_record_failure_and_propagate(tmp_path):
    trace = WorkflowTrace()
    trace.candidate_size = (720, 800)
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    with pytest.raises(framework.FileSecurityError):
        asyncio.run(
            workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
        )

    record_paths = list((tmp_path / "runs").glob("*/run-record.json"))
    assert len(record_paths) == 1
    record = RunRecord.model_validate_json(record_paths[0].read_text(encoding="utf-8"))
    assert record.final_result["status"] == "failed"


def test_real_execution_accepts_a_pillow_candidate_without_image_operation(tmp_path):
    trace = WorkflowTrace()
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    result = asyncio.run(workflow.run(["tea", "caffeine-free"]))

    assert result.status == framework.WorkflowStatus.passed
    assert result.candidate == trace.candidate_references[0]
    assert not any(event.kind == "image_operation" for event in result.model_events)
    assert trace.roles == [
        AgentRole.creative_director,
        AgentRole.layout_planner,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
    ]


def test_stale_review_cannot_approve_rerendered_candidate(tmp_path):
    trace = WorkflowTrace()
    trace.review_script = [
        {"decision": "fail", "action": "rerender"},
        {"stale_candidate": True},
    ]
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    with pytest.raises(framework.AgentIdentityMismatchError):
        asyncio.run(
            workflow.run(["tea", "caffeine-free"], execution_mode=ExecutionMode.fake)
        )

    assert trace.roles == [
        AgentRole.creative_director,
        AgentRole.layout_planner,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
        AgentRole.visual_producer,
        AgentRole.quality_critic,
    ]
    record_paths = list((tmp_path / "runs").glob("*/run-record.json"))
    assert len(record_paths) == 1
    record = RunRecord.model_validate_json(record_paths[0].read_text(encoding="utf-8"))
    assert record.final_result["status"] == "failed"


@pytest.mark.parametrize(
    ("image_format", "suffix", "media_type"),
    [
        ("PNG", ".png", "image/png"),
        ("JPEG", ".jpg", "image/jpeg"),
        ("JPEG", ".jpeg", "image/jpeg"),
        ("WEBP", ".webp", "image/webp"),
    ],
)
def test_default_real_mode_accepts_supported_image_formats(
    tmp_path,
    image_format,
    suffix,
    media_type,
):
    trace = WorkflowTrace()
    trace.candidate_format = image_format
    trace.candidate_suffix = suffix
    trace.candidate_media_type = media_type
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    result = asyncio.run(workflow.run(["tea", "caffeine-free"]))

    assert result.status == framework.WorkflowStatus.passed
    assert result.candidate.media_type == media_type
    assert result.candidate.uri.endswith(f"candidate{suffix}")
    session = framework.RunSession.load(result.record_path, root=tmp_path)
    image_data = session.file_store.read(result.candidate)
    with Image.open(BytesIO(image_data)) as image:
        assert image.format == image_format
        assert image.size == (720, 1280)
    assert hashlib.sha256(image_data).hexdigest() == result.candidate.sha256


def test_default_real_mode_rejects_a_media_type_that_disagrees_with_decoded_image(
    tmp_path,
):
    trace = WorkflowTrace()
    trace.candidate_format = "JPEG"
    trace.candidate_suffix = ".jpg"
    trace.candidate_media_type = "image/png"
    workflow = framework.PosterWorkflow(make_agent_set(trace), root=tmp_path)

    with pytest.raises(framework.FileSecurityError):
        asyncio.run(workflow.run(["tea", "caffeine-free"]))

    assert_failed_run_record(tmp_path, "FileSecurityError")

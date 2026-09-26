import asyncio

import poster_framework as framework
from poster_framework.fake import FakePosterModel
import pytest


class IndependentLayoutAgent:
    def __init__(self):
        self.output_format = framework.ContentFormat(name="example.layout", version="1")
        self.received_input = None
        self.identity = None

    async def run(self, inputs, context):
        self.received_input = inputs.content
        self.identity = context.identity
        return framework.AgentOutput(
            content={
                "headline": inputs.content["headline"],
                "blocks": ["headline", "copy"],
            },
            content_format=self.output_format,
        )


class CreativeAgentWithFile:
    def __init__(self):
        self.asset_reference = None

    async def run(self, inputs, context):
        self.asset_reference = context.files.write_bytes(
            context.files.invocation_path("source-material.json"),
            b"tea material source",
            purpose=framework.ArtifactPurpose.asset,
            media_type="application/json",
        )
        headline = " + ".join(item.text for item in context.original_request.keywords)
        return framework.AgentOutput(
            content={
                "headline": headline,
                "source_material": self.asset_reference.model_dump(mode="json"),
            },
            content_format=framework.FAKE_BRIEF_FORMAT,
            artifacts=(self.asset_reference,),
        )


class LayoutAgentWithFile:
    def __init__(self):
        self.output_format = framework.ContentFormat(name="example.file-layout", version="1")
        self.received_files = ()
        self.received_material = None

    async def run(self, inputs, context):
        self.received_files = inputs.metadata.files
        reference = framework.ArtifactReference.model_validate(inputs.content["source_material"])
        self.received_material = context.files.read(reference)
        return framework.AgentOutput(
            content={
                "headline": inputs.content["headline"],
                "source_material": self.received_material.decode("utf-8"),
            },
            content_format=self.output_format,
        )


def make_downstream_input(request, source_result, session, *, source_run_id=None, dependencies=None):
    reference = source_result.output_reference
    identity = framework.ExecutionIdentity(
        run_id=request.run_id,
        role=framework.AgentRole.layout_planner,
        invocation_id="invoke-f4-invalid-layout-input",
        execution_mode=framework.ExecutionMode.fake,
    )
    return framework.AgentInput(
        content=source_result.output.content,
        content_format=source_result.output.content_format,
        metadata=framework.FrameworkMetadata(
            identity=identity,
            original_request=request,
            version_dependencies=(
                dependencies
                if dependencies is not None
                else (
                    framework.VersionDependency(
                        artifact_id=reference.artifact_id,
                        revision=reference.revision,
                    ),
                )
            ),
            budget=session.budget,
            usage=session.usage,
        ),
        provenance=framework.ContentProvenance(
            run_id=source_run_id or request.run_id,
            source_id=reference.source_id,
            role=reference.role,
            invocation_id=reference.source_id,
            revision=reference.revision,
        ),
    )


def test_single_agent_entry_loads_request_and_upstream_output_for_downstream_use(tmp_path):
    request = framework.OriginalRequest.from_keywords(
        "run-f4-single-agent",
        ["tea", "caffeine-free"],
        {"tone": "calm"},
    )
    model = FakePosterModel(initial_remaining_revisions=0, delay_seconds=0)
    request_entry = getattr(framework, "run_single_agent_from_request", None)
    upstream_entry = getattr(framework, "run_single_agent_from_upstream", None)
    assert callable(request_entry)
    assert callable(upstream_entry)

    source_result = asyncio.run(
        request_entry(
            model.registration(framework.AgentRole.creative_director),
            request,
            root=tmp_path,
            execution_mode=framework.ExecutionMode.fake,
        )
    )

    layout_agent = IndependentLayoutAgent()
    layout_registration = framework.AgentRegistration(
        role=framework.AgentRole.layout_planner,
        instance_factory=lambda config: layout_agent,
        input_formats=frozenset({source_result.output.content_format}),
        output_formats=frozenset({layout_agent.output_format}),
    )
    downstream_result = asyncio.run(
        upstream_entry(
            layout_registration,
            source_result.record_path,
            root=tmp_path,
        )
    )

    assert source_result.output.content["headline"] == "tea + caffeine-free"
    assert source_result.output_reference.media_type == "application/json"
    assert downstream_result.output.content["blocks"] == ["headline", "copy"]
    assert downstream_result.output.content["headline"] == "tea + caffeine-free"
    assert layout_agent.received_input == source_result.output.content
    assert layout_agent.identity.role == framework.AgentRole.layout_planner
    assert layout_agent.identity.run_id == request.run_id
    assert layout_agent.identity.invocation_id != source_result.output_reference.source_id
    assert [call["role"] for call in model.calls] == [
        framework.AgentRole.creative_director.value
    ]
    saved = framework.RunRecord.model_validate_json(
        downstream_result.record_path.read_text(encoding="utf-8")
    )
    assert [event.identity.role for event in saved.model_events] == [
        framework.AgentRole.creative_director
    ]


def test_single_agent_upstream_imports_and_reads_exported_file_references(tmp_path):
    request = framework.OriginalRequest.from_keywords(
        "run-f4-single-file",
        ["tea", "caffeine-free"],
    )
    source_agent = CreativeAgentWithFile()
    source_registration = framework.AgentRegistration(
        role=framework.AgentRole.creative_director,
        instance_factory=lambda config: source_agent,
        input_formats=frozenset({framework.WORKFLOW_REQUEST_FORMAT}),
        output_formats=frozenset({framework.FAKE_BRIEF_FORMAT}),
    )
    source_result = asyncio.run(
        framework.run_single_agent_from_request(
            source_registration,
            request,
            root=tmp_path,
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    layout_agent = LayoutAgentWithFile()
    layout_registration = framework.AgentRegistration(
        role=framework.AgentRole.layout_planner,
        instance_factory=lambda config: layout_agent,
        input_formats=frozenset({framework.FAKE_BRIEF_FORMAT}),
        output_formats=frozenset({layout_agent.output_format}),
    )

    result = asyncio.run(
        framework.run_single_agent_from_upstream(
            layout_registration,
            source_result.record_path,
            root=tmp_path,
        )
    )

    assert layout_agent.received_material == b"tea material source"
    assert len(layout_agent.received_files) == 1
    assert layout_agent.received_files[0].file_id == source_agent.asset_reference.artifact_id
    assert result.output.content == {
        "headline": "tea + caffeine-free",
        "source_material": "tea material source",
    }
    saved_session = framework.RunSession.load(result.record_path, root=tmp_path)
    assert saved_session.file_store.read(source_agent.asset_reference) == b"tea material source"


@pytest.mark.parametrize(
    ("source_run_id", "dependencies", "error_type"),
    [
        (
            "another-run",
            None,
            framework.AgentIdentityMismatchError,
        ),
        (
            None,
            (),
            framework.AgentIdentityMismatchError,
        ),
    ],
)
def test_single_agent_rejects_upstream_identity_or_dependency_errors_and_saves_failure(
    tmp_path,
    source_run_id,
    dependencies,
    error_type,
):
    request = framework.OriginalRequest.from_keywords(
        f"run-f4-invalid-{source_run_id or 'dependency'}",
        ["tea", "caffeine-free"],
    )
    model = FakePosterModel(initial_remaining_revisions=0, delay_seconds=0)
    source_result = asyncio.run(
        framework.run_single_agent_from_request(
            model.registration(framework.AgentRole.creative_director),
            request,
            root=tmp_path,
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    session = framework.RunSession.load(source_result.record_path, root=tmp_path)
    inputs = make_downstream_input(
        request,
        source_result,
        session,
        source_run_id=source_run_id,
        dependencies=dependencies,
    )

    with pytest.raises(error_type):
        asyncio.run(
            framework.run_single_agent(
                model.registration(framework.AgentRole.layout_planner),
                inputs,
                session=session,
                record_path=source_result.record_path,
            )
        )

    saved = framework.RunRecord.model_validate_json(
        source_result.record_path.read_text(encoding="utf-8")
    )
    assert saved.final_result == {
        "status": "failed",
        "error_type": "AgentIdentityMismatchError",
    }
    assert [call["role"] for call in model.calls] == [
        framework.AgentRole.creative_director.value
    ]


def test_single_agent_upstream_rejects_a_changed_export_and_saves_failure(tmp_path):
    request = framework.OriginalRequest.from_keywords(
        "run-f4-changed-export",
        ["tea", "caffeine-free"],
    )
    model = FakePosterModel(initial_remaining_revisions=0, delay_seconds=0)
    source_result = asyncio.run(
        framework.run_single_agent_from_request(
            model.registration(framework.AgentRole.creative_director),
            request,
            root=tmp_path,
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    exported_path = tmp_path.joinpath(
        request.run_id,
        *source_result.output_reference.uri.split("/")[2:],
    )
    exported_path.write_bytes(b"changed export")

    with pytest.raises(framework.FileSecurityError):
        asyncio.run(
            framework.run_single_agent_from_upstream(
                model.registration(framework.AgentRole.layout_planner),
                source_result.record_path,
                root=tmp_path,
            )
        )

    saved = framework.RunRecord.model_validate_json(
        source_result.record_path.read_text(encoding="utf-8")
    )
    assert saved.final_result == {
        "status": "failed",
        "error_type": "FileSecurityError",
    }
    assert [call["role"] for call in model.calls] == [
        framework.AgentRole.creative_director.value
    ]


def test_single_agent_upstream_rejects_a_changed_file_reference_and_saves_failure(tmp_path):
    request = framework.OriginalRequest.from_keywords(
        "run-f4-changed-file-reference",
        ["tea", "caffeine-free"],
    )
    source_agent = CreativeAgentWithFile()
    source_registration = framework.AgentRegistration(
        role=framework.AgentRole.creative_director,
        instance_factory=lambda config: source_agent,
        input_formats=frozenset({framework.WORKFLOW_REQUEST_FORMAT}),
        output_formats=frozenset({framework.FAKE_BRIEF_FORMAT}),
    )
    source_result = asyncio.run(
        framework.run_single_agent_from_request(
            source_registration,
            request,
            root=tmp_path,
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    material_path = tmp_path.joinpath(
        request.run_id,
        *source_agent.asset_reference.uri.split("/")[2:],
    )
    material_path.write_bytes(b"changed source material")
    layout_agent = LayoutAgentWithFile()
    layout_registration = framework.AgentRegistration(
        role=framework.AgentRole.layout_planner,
        instance_factory=lambda config: layout_agent,
        input_formats=frozenset({framework.FAKE_BRIEF_FORMAT}),
        output_formats=frozenset({layout_agent.output_format}),
    )

    with pytest.raises(framework.FileSecurityError):
        asyncio.run(
            framework.run_single_agent_from_upstream(
                layout_registration,
                source_result.record_path,
                root=tmp_path,
            )
        )

    saved = framework.RunRecord.model_validate_json(
        source_result.record_path.read_text(encoding="utf-8")
    )
    assert saved.final_result == {
        "status": "failed",
        "error_type": "FileSecurityError",
    }
    assert layout_agent.received_files == ()

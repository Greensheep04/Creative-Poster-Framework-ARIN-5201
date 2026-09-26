from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .errors import AgentIdentityMismatchError, FileSecurityError
from .registration import AgentRegistration, AgentRunResult
from .runtime import RunRecord, RunSession
from .types import (
    AgentInput,
    AgentOutput,
    AgentRole,
    ArtifactPurpose,
    ArtifactReference,
    ContentProvenance,
    ExecutionIdentity,
    ExecutionMode,
    FileReference,
    FrameworkMetadata,
    OriginalRequest,
    VersionDependency,
)
from .workflow import WORKFLOW_REQUEST_FORMAT


@dataclass(frozen=True, slots=True)
class SingleAgentResult:
    agent_result: AgentRunResult
    output_reference: ArtifactReference
    record_path: Path

    @property
    def output(self) -> AgentOutput:
        return self.agent_result.output

    @property
    def metadata(self) -> FrameworkMetadata:
        return self.agent_result.metadata


async def run_single_agent_from_request(
    registration: AgentRegistration,
    request: OriginalRequest,
    *,
    root: str | Path,
    execution_mode: ExecutionMode = ExecutionMode.real,
    record_path: str | Path | None = None,
) -> SingleAgentResult:
    if not isinstance(request, OriginalRequest):
        raise TypeError("request must be an OriginalRequest")
    if not isinstance(execution_mode, ExecutionMode):
        raise TypeError("execution_mode must be an ExecutionMode")
    session = RunSession(request, root=root)
    target_path = Path(record_path) if record_path is not None else _default_record_path(
        root, request.run_id
    )
    identity = _new_identity(registration, session, execution_mode)
    inputs = AgentInput(
        content=request.model_dump(mode="json"),
        content_format=WORKFLOW_REQUEST_FORMAT,
        metadata=FrameworkMetadata(
            identity=identity,
            original_request=request,
            budget=session.budget,
            usage=session.usage,
        ),
        provenance=ContentProvenance(
            run_id=session.run_id,
            source_id=request.request_id,
        ),
    )
    return await run_single_agent(
        registration,
        inputs,
        session=session,
        record_path=target_path,
    )


async def run_single_agent_from_upstream(
    registration: AgentRegistration,
    source_record_path: str | Path,
    *,
    root: str | Path,
    record_path: str | Path | None = None,
) -> SingleAgentResult:
    source_path = Path(source_record_path)
    session = RunSession.load(source_path, root=root)
    target_path = Path(record_path) if record_path is not None else source_path
    try:
        record = RunRecord.model_validate_json(source_path.read_text(encoding="utf-8"))
        final_result = record.final_result
        if (
            not isinstance(final_result, dict)
            or final_result.get("status") != "single_agent_completed"
        ):
            raise AgentIdentityMismatchError(
                "upstream record has no exported single-agent result"
            )
        source_metadata = FrameworkMetadata.model_validate(final_result["metadata"])
        source_reference = ArtifactReference.model_validate(final_result["output_reference"])
        if (
            source_metadata.identity.run_id != session.run_id
            or source_metadata.identity.role != source_reference.role
            or source_metadata.identity.invocation_id != source_reference.source_id
            or source_reference.purpose != ArtifactPurpose.evidence
            or not source_reference.uri.endswith("agent-output.json")
        ):
            raise AgentIdentityMismatchError("upstream result identity does not match its export")
        source_output = AgentOutput.model_validate_json(session.file_store.read(source_reference))
        file_references = tuple(
            _to_file_reference(reference) for reference in source_output.artifacts
        )
        for reference in source_output.artifacts:
            session.file_store.read(reference)
        dependencies = (source_reference, *source_output.artifacts)
        identity = _new_identity(
            registration,
            session,
            source_metadata.identity.execution_mode,
        )
        inputs = AgentInput(
            content=source_output.content,
            content_format=source_output.content_format,
            metadata=FrameworkMetadata(
                identity=identity,
                original_request=session.original_request,
                version_dependencies=tuple(
                    VersionDependency(
                        artifact_id=reference.artifact_id,
                        revision=reference.revision,
                    )
                    for reference in dependencies
                ),
                files=file_references,
                budget=session.budget,
                usage=session.usage,
            ),
            provenance=ContentProvenance(
                run_id=session.run_id,
                source_id=source_reference.source_id,
                role=source_reference.role,
                invocation_id=source_reference.source_id,
                revision=source_reference.revision,
            ),
        )
    except Exception as error:
        session.set_final_result(
            {
                "status": "failed",
                "error_type": type(error).__name__,
            }
        )
        session.save(target_path)
        raise
    return await run_single_agent(
        registration,
        inputs,
        session=session,
        record_path=target_path,
    )


async def run_single_agent(
    registration: AgentRegistration,
    inputs: AgentInput,
    *,
    session: RunSession,
    record_path: str | Path,
) -> SingleAgentResult:
    if not isinstance(registration, AgentRegistration):
        raise TypeError("registration must be an AgentRegistration")
    if not isinstance(inputs, AgentInput):
        raise TypeError("inputs must be AgentInput")
    if not isinstance(session, RunSession):
        raise TypeError("session must be a RunSession")
    target_path = Path(record_path)
    try:
        _validate_upstream(registration, inputs, session)
        identity = inputs.metadata.identity
        context = session.create_context(identity, inputs.metadata)
        result = await registration.run_agent(inputs, context)
        if result.input_conversion is not None:
            session.record_conversion(result.input_conversion)
        if result.output_conversion is not None:
            session.record_conversion(result.output_conversion)
        encoded_output = json.dumps(
            result.output.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        output_reference = context.files.write_bytes(
            context.files.invocation_path("agent-output.json"),
            encoded_output,
            purpose=ArtifactPurpose.evidence,
            media_type="application/json",
        )
        session.memory.publish_artifact(
            _memory_name(registration.role),
            output_reference,
            result.output.content,
            result.output.content_format,
            publishing_identity=identity,
        )
        session.set_final_result(
            {
                "status": "single_agent_completed",
                "role": registration.role.value,
                "output": result.output.model_dump(mode="json"),
                "metadata": result.metadata.model_dump(mode="json"),
                "output_reference": output_reference.model_dump(mode="json"),
            }
        )
        session.save(target_path)
        return SingleAgentResult(
            agent_result=result,
            output_reference=output_reference,
            record_path=target_path,
        )
    except Exception as error:
        session.set_final_result(
            {
                "status": "failed",
                "error_type": type(error).__name__,
            }
        )
        session.save(target_path)
        raise


def _validate_upstream(
    registration: AgentRegistration,
    inputs: AgentInput,
    session: RunSession,
) -> None:
    metadata = inputs.metadata
    identity = metadata.identity
    request = session.original_request
    if identity.run_id != session.run_id or metadata.original_request != request:
        raise AgentIdentityMismatchError("single-agent input belongs to another run")
    if identity.role != registration.role:
        raise AgentIdentityMismatchError("single-agent input role does not match its registration")
    provenance = inputs.provenance
    if provenance is None:
        raise AgentIdentityMismatchError("single-agent input requires source provenance")
    if provenance.run_id != session.run_id:
        raise AgentIdentityMismatchError("single-agent input source belongs to another run")
    if provenance.role is None:
        if (
            provenance.source_id != request.request_id
            or provenance.invocation_id is not None
            or provenance.revision is not None
        ):
            raise AgentIdentityMismatchError("original request provenance is invalid")
    else:
        if (
            provenance.invocation_id != provenance.source_id
            or provenance.revision is None
        ):
            raise AgentIdentityMismatchError("upstream invocation provenance is incomplete")
        source_references = [
            reference
            for reference in session.file_store.artifacts
            if reference.role == provenance.role
            and reference.source_id == provenance.source_id
            and reference.revision == provenance.revision
            and reference.purpose == ArtifactPurpose.evidence
            and reference.uri.endswith("agent-output.json")
        ]
        if len(source_references) != 1:
            raise AgentIdentityMismatchError("upstream source does not identify one exported result")
        source_reference = source_references[0]
        if not _has_dependency(metadata.version_dependencies, source_reference):
            raise AgentIdentityMismatchError("upstream result is absent from version dependencies")
        source_output = AgentOutput.model_validate_json(
            session.file_store.read(source_reference)
        )
        if (
            source_output.content != inputs.content
            or source_output.content_format != inputs.content_format
        ):
            raise AgentIdentityMismatchError("single-agent input differs from its exported source")
    for file_reference in metadata.files:
        if file_reference.run_id != session.run_id:
            raise AgentIdentityMismatchError("single-agent file belongs to another run")
        artifact = session.file_store.reference(file_reference.file_id)
        if (
            artifact.run_id != file_reference.run_id
            or artifact.uri != file_reference.uri
            or artifact.revision != file_reference.revision
            or artifact.sha256 != file_reference.sha256
            or artifact.media_type != file_reference.media_type
        ):
            raise FileSecurityError("single-agent file reference does not match its saved artifact")
        if not _has_dependency(metadata.version_dependencies, artifact):
            raise AgentIdentityMismatchError("single-agent file is absent from version dependencies")
        session.file_store.read(artifact)


def _has_dependency(
    dependencies: tuple[VersionDependency, ...],
    reference: ArtifactReference,
) -> bool:
    return any(
        dependency.artifact_id == reference.artifact_id
        and dependency.revision == reference.revision
        for dependency in dependencies
    )


def _memory_name(role: AgentRole) -> str:
    return {
        AgentRole.creative_director: "brief",
        AgentRole.layout_planner: "layout",
        AgentRole.visual_producer: "visual_output",
        AgentRole.quality_critic: "quality_review",
    }[role]


def _default_record_path(root: str | Path, run_id: str) -> Path:
    return Path(root) / "runs" / run_id / "run-record.json"


def _new_identity(
    registration: AgentRegistration,
    session: RunSession,
    execution_mode: ExecutionMode,
) -> ExecutionIdentity:
    if not isinstance(registration, AgentRegistration):
        raise TypeError("registration must be an AgentRegistration")
    return ExecutionIdentity(
        run_id=session.run_id,
        role=registration.role,
        invocation_id=f"invoke-{uuid4().hex}",
        execution_mode=execution_mode,
    )


def _to_file_reference(reference: ArtifactReference) -> FileReference:
    return FileReference(
        file_id=reference.artifact_id,
        run_id=reference.run_id,
        uri=reference.uri,
        revision=reference.revision,
        sha256=reference.sha256,
        media_type=reference.media_type,
    )


__all__ = [
    "SingleAgentResult",
    "run_single_agent",
    "run_single_agent_from_request",
    "run_single_agent_from_upstream",
]

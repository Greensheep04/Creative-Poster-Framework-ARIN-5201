from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from pathlib import PurePosixPath
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from PIL import Image
from pydantic import BaseModel, ConfigDict

from .errors import (
    AgentIdentityMismatchError,
    BudgetExceededError,
    ContentFormatCompatibilityError,
    FileSecurityError,
    InterfaceVersionCompatibilityError,
)
from .generation import ArtifactGenerationRecord
from .registration import AgentSet, AgentSetSnapshot, AgentRegistrationSnapshot
from .runtime import ModelCallEvent, RunSession
from .tools import ToolRegistry
from .types import (
    INTERFACE_VERSION,
    AgentInput,
    AgentOutput,
    AgentRole,
    ArtifactPurpose,
    ArtifactReference,
    ContentFormat,
    ContentProvenance,
    ExecutionIdentity,
    ExecutionMode,
    FileReference,
    FrameworkMetadata,
    JSONValue,
    KeywordReviewStatus,
    OriginalRequest,
    ReviewDecision,
    ReviewRecord,
    RepairAuthorization,
    RevisionAction,
    RunBudget,
    RunUsage,
    VersionDependency,
)


RevisionBusinessValidator = Callable[
    [JSONValue, JSONValue, RevisionAction, OriginalRequest], None
]
MAX_GRAPH_STEPS_PER_ROUND = 6


WORKFLOW_REQUEST_FORMAT = ContentFormat(
    name="poster_framework.workflow_request",
    version="1",
)
FAKE_CANDIDATE_MEDIA_TYPE = "application/vnd.poster-framework.fake-candidate+json"


class WorkflowStatus(StrEnum):
    passed = "passed"
    needs_review = "needs_review"


@dataclass(frozen=True, slots=True)
class WorkflowStage:
    output: AgentOutput
    identity: ExecutionIdentity
    record_reference: ArtifactReference


class WorkflowResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    original_request: OriginalRequest
    status: WorkflowStatus
    brief: AgentOutput | None
    layout: AgentOutput | None
    visual: AgentOutput | None
    review: ReviewRecord | None
    candidate: ArtifactReference | None
    assets: tuple[ArtifactReference, ...]
    artifacts: tuple[ArtifactReference, ...]
    usage: RunUsage
    model_events: tuple[ModelCallEvent, ...]
    call_sequence: tuple[AgentRole, ...]
    revision_rounds: int
    reason: str | None
    record_path: Path


class _WorkflowState(TypedDict, total=False):
    run_id: str
    request: OriginalRequest
    execution_mode: ExecutionMode
    agents: AgentSetSnapshot
    session: RunSession
    brief: WorkflowStage
    layout: WorkflowStage
    visual: WorkflowStage
    review_stage: WorkflowStage
    review: ReviewRecord
    candidate: ArtifactReference
    previous_candidate: ArtifactReference
    assets: list[ArtifactReference]
    previous_assets: list[ArtifactReference]
    asset_generation_records: list[ArtifactGenerationRecord | None]
    previous_asset_generation_records: list[ArtifactGenerationRecord | None]
    revision_before_result: JSONValue
    revision_validator: RevisionBusinessValidator | None
    repair_authorization: RepairAuthorization | None
    revision_action: RevisionAction
    revision_target: VersionDependency
    revision_rounds: int
    review_signatures: list[str]
    call_sequence: list[AgentRole]
    status: WorkflowStatus
    stop_reason: str
    stop: bool
    next_role: str


class PosterWorkflow:
    def __init__(
        self,
        agent_set: AgentSet,
        *,
        root: str | Path,
        tool_registry: ToolRegistry | None = None,
        budget: RunBudget | None = None,
        configuration: Mapping[str, object] | None = None,
        revision_validator: RevisionBusinessValidator | None = None,
        repair_authorizer: Callable | None = None,
    ) -> None:
        if not isinstance(agent_set, AgentSet):
            raise TypeError("agent_set must be an AgentSet")
        if tool_registry is not None and not isinstance(tool_registry, ToolRegistry):
            raise TypeError("tool_registry must be a ToolRegistry")
        if budget is not None and not isinstance(budget, RunBudget):
            raise TypeError("budget must be a RunBudget")
        if configuration is not None and not isinstance(configuration, Mapping):
            raise TypeError("configuration must be a mapping")
        if revision_validator is not None and not callable(revision_validator):
            raise TypeError("revision_validator must be callable")
        if repair_authorizer is not None and not callable(repair_authorizer):
            raise TypeError("repair_authorizer must be callable")
        self._repair_authorizer = repair_authorizer
        self._agent_set = agent_set
        self._root = Path(root)
        self._tool_registry = tool_registry
        self._budget = budget or RunBudget()
        self._configuration = copy.deepcopy(dict(configuration or {}))
        self._revision_validator = revision_validator
        self._graph = self._build_graph()

    async def run(
        self,
        raw_keywords: Sequence[str],
        *,
        constraints: Mapping[str, object] | None = None,
        execution_mode: ExecutionMode = ExecutionMode.real,
        budget: RunBudget | None = None,
    ) -> WorkflowResult:
        run_id = f"run-{uuid4().hex}"
        record_path = self._record_path(run_id)
        session: RunSession | None = None
        try:
            if not isinstance(execution_mode, ExecutionMode):
                raise TypeError("execution_mode must be an ExecutionMode")
            run_budget = budget or self._budget
            if not isinstance(run_budget, RunBudget):
                raise TypeError("budget must be a RunBudget")
            request = OriginalRequest.from_keywords(
                run_id,
                raw_keywords,
                constraints,
            )
            revision_validator = self._revision_validator
            agents = self._agent_set.snapshot()
            configuration = copy.deepcopy(self._configuration)
            configuration["agent_configurations"] = {
                role.value: agents.registration_for(role).config
                for role in AgentRole
            }
            configuration["execution_mode"] = execution_mode.value
            session = RunSession(
                request,
                root=self._root,
                tool_registry=self._tool_registry,
                budget=run_budget,
                configuration=configuration,
            )
            self._preflight(agents)
            initial_state: _WorkflowState = {
                "run_id": run_id,
                "request": request,
                "execution_mode": execution_mode,
                "agents": agents,
                "session": session,
                "assets": [],
                "previous_assets": [],
                "asset_generation_records": [],
                "previous_asset_generation_records": [],
                "revision_rounds": 0,
                "review_signatures": [],
                "call_sequence": [],
                "revision_validator": revision_validator,
                "stop": False,
            }
            recursion_limit = max(
                25,
                MAX_GRAPH_STEPS_PER_ROUND
                * (run_budget.max_revision_rounds + 1)
                + 1,
            )
            final_state = await self._graph.ainvoke(
                initial_state,
                config={"recursion_limit": recursion_limit},
            )
            result = self._result(final_state, record_path)
            session.set_final_result(result.model_dump(mode="json"))
            session.save(record_path)
            return result
        except Exception as error:
            if session is None:
                self._save_unstarted_failure(run_id, record_path, error)
            else:
                session.set_final_result(
                    {
                        "status": "failed",
                        "error_type": type(error).__name__,
                    }
                )
                session.save(record_path)
            raise

    def _record_path(self, run_id: str) -> Path:
        return self._root / "runs" / run_id / "run-record.json"

    def _save_unstarted_failure(
        self,
        run_id: str,
        record_path: Path,
        error: Exception,
    ) -> None:
        record_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path = record_path.with_name("failure.json")
        failure_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "status": "failed",
                    "error_type": type(error).__name__,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _build_graph(self):
        graph = StateGraph(_WorkflowState)
        graph.add_node("creative_director", self._creative_director)
        graph.add_node("layout_planner", self._layout_planner)
        graph.add_node("visual_producer", self._visual_producer)
        graph.add_node("quality_critic", self._quality_critic)
        graph.add_node("judge", self._judge)
        graph.add_node("route", self._route)
        graph.add_edge(START, "creative_director")
        graph.add_conditional_edges(
            "creative_director",
            self._continue_after_role,
            {"continue": "layout_planner", "end": END},
        )
        graph.add_conditional_edges(
            "layout_planner",
            self._continue_after_role,
            {"continue": "visual_producer", "end": END},
        )
        graph.add_conditional_edges(
            "visual_producer",
            self._continue_after_role,
            {"continue": "quality_critic", "end": END},
        )
        graph.add_conditional_edges(
            "quality_critic",
            self._continue_after_role,
            {"continue": "judge", "end": END},
        )
        graph.add_edge("judge", "route")
        graph.add_conditional_edges(
            "route",
            self._next_role,
            {
                AgentRole.creative_director.value: "creative_director",
                AgentRole.layout_planner.value: "layout_planner",
                AgentRole.visual_producer.value: "visual_producer",
                "end": END,
            },
        )
        return graph.compile()

    @staticmethod
    def _continue_after_role(state: _WorkflowState) -> str:
        return "end" if state.get("stop", False) else "continue"

    @staticmethod
    def _next_role(state: _WorkflowState) -> str:
        return state.get("next_role", "end")

    @staticmethod
    def _accepts_input(
        registration: AgentRegistrationSnapshot,
        content_format: ContentFormat,
    ) -> bool:
        return content_format in registration.input_formats or any(
            adapter.source_format == content_format
            for adapter in registration.input_adapters
        )

    @classmethod
    def _preflight(cls, agents: AgentSetSnapshot) -> None:
        registrations = {
            role: agents.registration_for(role)
            for role in AgentRole
        }
        for role, registration in registrations.items():
            if INTERFACE_VERSION not in registration.interface_versions:
                raise InterfaceVersionCompatibilityError(
                    f"interface version {INTERFACE_VERSION!r} is unsupported by "
                    f"{role.value}"
                )
        director = registrations[AgentRole.creative_director]
        if not cls._accepts_input(director, WORKFLOW_REQUEST_FORMAT):
            raise ContentFormatCompatibilityError(
                "creative_director cannot consume the workflow request format"
            )
        route = (
            AgentRole.creative_director,
            AgentRole.layout_planner,
            AgentRole.visual_producer,
            AgentRole.quality_critic,
        )
        for producer_role, consumer_role in zip(route, route[1:]):
            producer = registrations[producer_role]
            consumer = registrations[consumer_role]
            for source_format in producer.output_formats:
                if cls._accepts_input(consumer, source_format):
                    continue
                converted_formats = {
                    adapter.target_format
                    for adapter in producer.output_adapters
                    if adapter.source_format == source_format
                }
                if not any(
                    cls._accepts_input(consumer, target_format)
                    for target_format in converted_formats
                ):
                    raise ContentFormatCompatibilityError(
                        f"no format path connects {producer_role.value} output "
                        f"{source_format.name}@{source_format.version} to "
                        f"{consumer_role.value}"
                    )

    async def _creative_director(self, state: _WorkflowState) -> dict[str, object]:
        return await self._run_role(state, AgentRole.creative_director)

    async def _layout_planner(self, state: _WorkflowState) -> dict[str, object]:
        return await self._run_role(state, AgentRole.layout_planner)

    async def _visual_producer(self, state: _WorkflowState) -> dict[str, object]:
        return await self._run_role(state, AgentRole.visual_producer)

    async def _quality_critic(self, state: _WorkflowState) -> dict[str, object]:
        return await self._run_role(state, AgentRole.quality_critic)

    async def _run_role(
        self,
        state: _WorkflowState,
        role: AgentRole,
    ) -> dict[str, object]:
        session = state["session"]
        agents = state["agents"]
        invocation_id = f"invoke-{uuid4().hex}"
        identity = ExecutionIdentity(
            run_id=state["run_id"],
            role=role,
            invocation_id=invocation_id,
            execution_mode=state["execution_mode"],
        )
        metadata = self._metadata_for(state, identity)
        inputs = self._inputs_for(state, role, metadata)
        context = session.create_context(identity, metadata)
        sequence = [*state.get("call_sequence", []), role]
        try:
            agent_result = await agents.run_agent(role, inputs, context)
        except BudgetExceededError as error:
            return {
                "call_sequence": sequence,
                "status": WorkflowStatus.needs_review,
                "stop_reason": str(error),
                "stop": True,
            }
        if agent_result.input_conversion is not None:
            session.record_conversion(agent_result.input_conversion)
        if agent_result.output_conversion is not None:
            session.record_conversion(agent_result.output_conversion)
        output = agent_result.output
        output_conversion = self._convert_output_for_next_role(
            agents,
            role,
            output,
            agent_result.metadata,
        )
        if output_conversion is not None:
            output, conversion_record = output_conversion
            session.record_conversion(conversion_record)
        encoded_output = json.dumps(
            output.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        output_record = context.files.write_bytes(
            context.files.invocation_path("agent-output.json"),
            encoded_output,
            purpose=ArtifactPurpose.evidence,
            media_type="application/json",
        )
        session.memory.publish_artifact(
            self._memory_name(role),
            output_record,
            output.content,
            output.content_format,
            publishing_identity=identity,
        )
        stage = WorkflowStage(
            output=output,
            identity=identity,
            record_reference=output_record,
        )
        update: dict[str, object] = {
            self._state_name(role): stage,
            "call_sequence": sequence,
            "stop": False,
        }
        if role == AgentRole.visual_producer:
            candidate, assets, generation_records = self._record_visual_output(
                state,
                output,
                identity,
                context,
            )
            session.memory.publish_artifact(
                "candidate",
                candidate,
                output.content,
                output.content_format,
                publishing_identity=identity,
            )
            update["candidate"] = candidate
            update["assets"] = assets
            update["asset_generation_records"] = generation_records
        elif role == AgentRole.quality_critic:
            if output.review is not None:
                session.memory.publish_artifact(
                    "quality_review",
                    output_record,
                    output.content,
                    output.content_format,
                    publishing_identity=identity,
                )
                update["review"] = output.review
        self._validate_revision_business(state, update, role)
        return update

    @staticmethod
    def _state_name(role: AgentRole) -> str:
        return {
            AgentRole.creative_director: "brief",
            AgentRole.layout_planner: "layout",
            AgentRole.visual_producer: "visual",
            AgentRole.quality_critic: "review_stage",
        }[role]

    @staticmethod
    def _memory_name(role: AgentRole) -> str:
        return {
            AgentRole.creative_director: "brief",
            AgentRole.layout_planner: "layout",
            AgentRole.visual_producer: "visual_output",
            AgentRole.quality_critic: "quality_review",
        }[role]

    def _inputs_for(
        self,
        state: _WorkflowState,
        role: AgentRole,
        metadata: FrameworkMetadata,
    ) -> AgentInput:
        if role == AgentRole.creative_director:
            request = state["request"]
            content = request.model_dump(mode="json")
            content_format = WORKFLOW_REQUEST_FORMAT
            provenance = ContentProvenance(
                run_id=request.run_id,
                source_id=request.request_id,
            )
        elif role == AgentRole.layout_planner:
            source = state.get("brief")
            if source is None:
                raise AgentIdentityMismatchError("layout_planner requires a current brief")
            content = source.output.content
            content_format = source.output.content_format
            provenance = self._stage_provenance(source)
        elif role == AgentRole.visual_producer:
            source = state.get("layout")
            if source is None:
                raise AgentIdentityMismatchError("visual_producer requires a current layout")
            content = source.output.content
            content_format = source.output.content_format
            provenance = self._stage_provenance(source)
        else:
            source = state.get("visual")
            if source is None:
                raise AgentIdentityMismatchError("quality_critic requires a current visual output")
            content = source.output.content
            content_format = source.output.content_format
            provenance = self._stage_provenance(source)
        return AgentInput(
            content=copy.deepcopy(content),
            content_format=content_format,
            metadata=metadata,
            provenance=provenance,
        )

    @staticmethod
    def _stage_provenance(stage: WorkflowStage) -> ContentProvenance:
        return ContentProvenance(
            run_id=stage.identity.run_id,
            source_id=stage.identity.invocation_id,
            role=stage.identity.role,
            invocation_id=stage.identity.invocation_id,
            revision=stage.record_reference.revision,
        )

    def _metadata_for(
        self,
        state: _WorkflowState,
        identity: ExecutionIdentity,
    ) -> FrameworkMetadata:
        dependencies: list[VersionDependency] = []
        stages = (state.get("brief"), state.get("layout"), state.get("visual"))
        for stage in stages:
            if stage is not None:
                dependencies.append(
                    VersionDependency(
                        artifact_id=stage.record_reference.artifact_id,
                        revision=stage.record_reference.revision,
                    )
                )
        image_references = self._file_references_for(state, identity.role)
        dependencies.extend(
            VersionDependency(artifact_id=item.file_id, revision=item.revision)
            for item in image_references
        )
        unique_dependencies = {
            (item.artifact_id, item.revision): item for item in dependencies
        }
        return FrameworkMetadata(
            identity=identity,
            original_request=state["request"],
            version_dependencies=tuple(unique_dependencies.values()),
            files=image_references,
            review_route=(AgentRole.quality_critic,),
            revision_action=state.get("revision_action"),
            revision_target=state.get("revision_target"),
            usage=state["session"].usage,
            budget=state["session"].budget,
        )

    @staticmethod
    def _file_reference(reference: ArtifactReference) -> FileReference:
        return FileReference(
            file_id=reference.artifact_id,
            run_id=reference.run_id,
            uri=reference.uri,
            revision=reference.revision,
            sha256=reference.sha256,
            media_type=reference.media_type,
        )

    def _file_references_for(
        self,
        state: _WorkflowState,
        role: AgentRole,
    ) -> tuple[FileReference, ...]:
        references: list[ArtifactReference] = []
        references.extend(state.get("assets", []))
        candidate = state.get("candidate")
        if (
            candidate is None
            and role == AgentRole.visual_producer
            and state.get("revision_action") == RevisionAction.rerender
        ):
            candidate = state.get("previous_candidate")
        if candidate is not None and role in {
            AgentRole.visual_producer,
            AgentRole.quality_critic,
        }:
            references.append(candidate)
        unique = {item.artifact_id: item for item in references}
        return tuple(self._file_reference(item) for item in unique.values())

    def _convert_output_for_next_role(
        self,
        agents: AgentSetSnapshot,
        role: AgentRole,
        output: AgentOutput,
        metadata: FrameworkMetadata,
    ) -> tuple[AgentOutput, object] | None:
        route = (
            AgentRole.creative_director,
            AgentRole.layout_planner,
            AgentRole.visual_producer,
            AgentRole.quality_critic,
        )
        position = route.index(role)
        if position == len(route) - 1:
            return None
        next_registration = agents.registration_for(route[position + 1])
        if self._accepts_input(next_registration, output.content_format):
            return None
        registration = agents.registration_for(role)
        target_format = next(
            (
                adapter.target_format
                for adapter in registration.output_adapters
                if adapter.source_format == output.content_format
                and self._accepts_input(next_registration, adapter.target_format)
            ),
            None,
        )
        if target_format is None:
            raise ContentFormatCompatibilityError(
                f"no output conversion reaches {route[position + 1].value}"
            )
        prepared = registration.prepare_output(output, metadata, target_format)
        if prepared.conversion is None:
            return None
        return prepared.output, prepared.conversion

    def _record_visual_output(
        self,
        state: _WorkflowState,
        output: AgentOutput,
        identity: ExecutionIdentity,
        context: object,
    ) -> tuple[
        ArtifactReference,
        list[ArtifactReference],
        list[ArtifactGenerationRecord],
    ]:
        session = state["session"]
        candidates = [
            reference
            for reference in output.artifacts
            if reference.purpose == ArtifactPurpose.candidate
        ]
        if len(candidates) != 1:
            raise FileSecurityError("visual_producer must return exactly one candidate file")
        candidate = candidates[0]
        if candidate.role != AgentRole.visual_producer:
            raise FileSecurityError("candidate must belong to visual_producer")
        if not self._was_published_by_invocation(candidate, identity):
            raise FileSecurityError("candidate must be created by the current invocation")
        candidate_data = session.file_store.read(candidate)
        if candidate.media_type == FAKE_CANDIDATE_MEDIA_TYPE:
            if identity.execution_mode != ExecutionMode.fake:
                raise FileSecurityError("Fake candidate files require fake execution mode")
            self._validate_fake_candidate(candidate_data, output.content)
        else:
            self._validate_candidate_image(candidate_data, candidate)
        assets = [
            reference
            for reference in output.artifacts
            if reference.purpose == ArtifactPurpose.asset
        ]
        if any(reference.role != AgentRole.visual_producer for reference in assets):
            raise FileSecurityError("visual assets must belong to visual_producer")
        for reference in (*assets, candidate):
            session.file_store.read(reference)
        current_generation_records = list(
            session.file_store.generation_records_for_invocation(identity.invocation_id)
        )
        asset_keys = {(item.artifact_id, item.revision) for item in assets}
        if any(
            (record.reference.artifact_id, record.reference.revision) not in asset_keys
            for record in current_generation_records
        ):
            raise FileSecurityError(
                "registered asset generation metadata must be returned with its artifact"
            )
        records_by_reference = {
            (item.reference.artifact_id, item.reference.revision): item
            for item in current_generation_records
        }
        current_action = state.get("revision_action")
        prior_assets = list(state.get("previous_assets", []))
        prior_generation_records = list(state.get("previous_asset_generation_records", []))
        if len(prior_generation_records) != len(prior_assets):
            prior_generation_records = self._generation_records_for_assets(state, prior_assets)
        if current_action == RevisionAction.rerender:
            if any(item not in prior_assets for item in assets):
                raise FileSecurityError("rerender cannot replace retained source assets")
            selected_assets = prior_assets
            selected_generation_records = prior_generation_records
        elif current_action == RevisionAction.regenerate_asset:
            target = state["revision_target"]
            target_dependency = target
            target_asset = next(
                item
                for item in prior_assets
                if item.artifact_id == target.artifact_id
                and item.revision == target.revision
            )
            replacement_records = [
                item
                for item in current_generation_records
                if item.replaces == target_dependency
            ]
            if len(replacement_records) != 1:
                raise FileSecurityError(
                    "regenerate_asset must publish one new file for its selected target"
                )
            replacement_record = replacement_records[0]
            replacement = replacement_record.reference
            if (
                replacement not in assets
                or replacement.artifact_id == target_asset.artifact_id
                or not self._was_published_by_invocation(replacement, identity)
            ):
                raise FileSecurityError(
                    "regenerate_asset replacement must be a new file from this invocation"
                )
            new_assets = [
                item
                for item in assets
                if self._was_published_by_invocation(item, identity)
            ]
            if new_assets != [replacement]:
                raise FileSecurityError("regenerate_asset may replace only its selected asset")
            if any(item not in prior_assets and item != replacement for item in assets):
                raise FileSecurityError(
                    "regenerate_asset cannot change an unspecified asset reference"
                )
            selected_assets = [
                replacement if item == target_asset else item for item in prior_assets
            ]
            selected_generation_records = [
                replacement_record if item == target_asset else record
                for item, record in zip(prior_assets, prior_generation_records)
            ]
        elif current_action == RevisionAction.revise_layout:
            current_layout = state.get("layout")
            if current_layout is None:
                raise AgentIdentityMismatchError("layout revision did not produce a current layout")
            selected_assets, selected_generation_records = self._select_layout_assets(
                prior_assets,
                prior_generation_records,
                assets,
                current_generation_records,
                records_by_reference,
                identity,
            )
        else:
            selected_assets = assets
            selected_generation_records = [
                records_by_reference.get((item.artifact_id, item.revision))
                or session.file_store.generation_record(item)
                for item in selected_assets
            ]
        self._publish_assets(
            state,
            identity,
            output.content_format,
            selected_assets,
        )
        return candidate, selected_assets, selected_generation_records

    @staticmethod
    def _was_published_by_invocation(
        reference: ArtifactReference,
        identity: ExecutionIdentity,
    ) -> bool:
        prefix = (
            f"runs/{identity.run_id}/invocations/"
            f"{identity.role.value}/{identity.invocation_id}/"
        )
        return (
            reference.run_id == identity.run_id
            and reference.role == identity.role
            and reference.source_id == identity.invocation_id
            and reference.uri.startswith(prefix)
        )

    @staticmethod
    def _generation_records_for_assets(
        state: _WorkflowState,
        assets: Sequence[ArtifactReference],
    ) -> list[ArtifactGenerationRecord | None]:
        session = state["session"]
        return [session.file_store.generation_record(item) for item in assets]

    @classmethod
    def _select_layout_assets(
        cls,
        previous: Sequence[ArtifactReference],
        previous_records: Sequence[ArtifactGenerationRecord | None],
        current: Sequence[ArtifactReference],
        current_records: Sequence[ArtifactGenerationRecord],
        records_by_reference: Mapping[tuple[str, str], ArtifactGenerationRecord],
        identity: ExecutionIdentity,
    ) -> tuple[list[ArtifactReference], list[ArtifactGenerationRecord]]:
        previous_by_dependency = {
            (item.artifact_id, item.revision): item for item in previous
        }
        replacements: dict[tuple[str, str], ArtifactGenerationRecord] = {}
        for record in current_records:
            if record.replaces is None:
                continue
            target = (record.replaces.artifact_id, record.replaces.revision)
            if target not in previous_by_dependency or target in replacements:
                raise FileSecurityError(
                    "layout revision contains an invalid or repeated asset replacement target"
                )
            replacements[target] = record

        current_keys = {(item.artifact_id, item.revision) for item in current}
        if any(
            (record.reference.artifact_id, record.reference.revision) not in current_keys
            for record in current_records
        ):
            raise FileSecurityError("layout generation records must identify returned assets")

        selected_assets: list[ArtifactReference] = []
        selected_records: list[ArtifactGenerationRecord | None] = []
        used_current: set[tuple[str, str]] = set()
        for previous_reference, previous_record in zip(previous, previous_records):
            previous_key = (previous_reference.artifact_id, previous_reference.revision)
            replacement_record = replacements.get(previous_key)
            if replacement_record is not None:
                replacement = replacement_record.reference
                if replacement == previous_reference:
                    raise FileSecurityError("layout replacement must publish a new asset file")
                old_fingerprint = previous_record.fingerprint if previous_record else None
                new_fingerprint = replacement_record.fingerprint
                if (
                    old_fingerprint is not None
                    and new_fingerprint is not None
                    and old_fingerprint.key == new_fingerprint.key
                ):
                    selected_assets.append(previous_reference)
                    selected_records.append(previous_record)
                else:
                    selected_assets.append(replacement)
                    selected_records.append(replacement_record)
                used_current.add(
                    (replacement.artifact_id, replacement.revision)
                )
                continue

            current_record = records_by_reference.get(previous_key)
            old_fingerprint = previous_record.fingerprint if previous_record else None
            if (
                current_record is None
                or old_fingerprint is None
                or current_record.fingerprint is None
            ):
                raise FileSecurityError(
                    "layout revision cannot reuse an asset without both fingerprints"
                )
            if old_fingerprint.key != current_record.fingerprint.key:
                raise FileSecurityError(
                    "layout revision cannot reuse an asset with changed generation conditions"
                )
            selected_assets.append(previous_reference)
            selected_records.append(previous_record or current_record)
            used_current.add(previous_key)

        for reference in current:
            key = (reference.artifact_id, reference.revision)
            if key in used_current or key in previous_by_dependency:
                continue
            record = records_by_reference.get(key)
            if record is None and not cls._was_published_by_invocation(reference, identity):
                raise FileSecurityError(
                    "new layout assets must be published or registered by this invocation"
                )
            selected_assets.append(reference)
            selected_records.append(record)
        return selected_assets, selected_records

    def _publish_assets(
        self,
        state: _WorkflowState,
        identity: ExecutionIdentity,
        content_format: ContentFormat,
        assets: Sequence[ArtifactReference],
    ) -> None:
        session = state["session"]
        selected_names = {self._memory_asset_name(item) for item in assets}
        previous_assets = state.get("previous_assets", [])
        for reference in previous_assets:
            name = self._memory_asset_name(reference)
            if name not in selected_names:
                session.memory.invalidate_artifact(name)
        for reference in assets:
            data = session.file_store.read(reference)
            session.memory.publish_artifact(
                self._memory_asset_name(reference),
                reference,
                {"sha256": hashlib.sha256(data).hexdigest(), "media_type": reference.media_type},
                content_format,
                publishing_identity=identity,
            )

    def _validate_revision_business(
        self,
        state: _WorkflowState,
        update: Mapping[str, object],
        role: AgentRole,
    ) -> None:
        action = state.get("revision_action")
        if action is None:
            return
        expected_role = {
            RevisionAction.revise_brief: AgentRole.creative_director,
            RevisionAction.revise_layout: AgentRole.layout_planner,
            RevisionAction.regenerate_asset: AgentRole.visual_producer,
            RevisionAction.rerender: AgentRole.visual_producer,
        }[action]
        if role != expected_role:
            return
        validator = state.get("revision_validator")
        if validator is None:
            return
        before = state.get("revision_before_result")
        if before is None:
            raise AgentIdentityMismatchError("revision validation requires the prior result")
        validator(
            copy.deepcopy(before),
            self._published_result({**state, **update}),
            action,
            state["request"].model_copy(deep=True),
        )

    @staticmethod
    def _published_result(state: Mapping[str, object]) -> JSONValue:
        request = state["request"]
        brief = state.get("brief")
        layout = state.get("layout")
        visual = state.get("visual")
        candidate = state.get("candidate")
        return {
            "run_id": state["run_id"],
            "original_request": request.model_dump(mode="json"),
            "brief": copy.deepcopy(brief.output.content) if brief is not None else None,
            "layout": copy.deepcopy(layout.output.content) if layout is not None else None,
            "visual": copy.deepcopy(visual.output.content) if visual is not None else None,
            "candidate": candidate.model_dump(mode="json") if candidate is not None else None,
            "assets": [
                item.model_dump(mode="json")
                for item in state.get("assets", [])
            ],
        }

    @staticmethod
    def _memory_asset_name(reference: ArtifactReference) -> str:
        return f"asset:{reference.artifact_id}:{reference.revision}"

    @staticmethod
    def _validate_fake_candidate(candidate_data: bytes, content: JSONValue) -> None:
        value = json.loads(candidate_data)
        if not isinstance(value, dict) or set(value) != {
            "image_text",
            "remaining_revisions",
        }:
            raise FileSecurityError("Fake candidate file must use the fixed JSON template")
        remaining = value["remaining_revisions"]
        text_by_remaining = {
            0: "可以通过审核的图片",
            1: "需要修改的图片",
            2: "需要修改两次的图片",
            3: "需要修改三次的图片",
        }
        if (
            isinstance(remaining, bool)
            or not isinstance(remaining, int)
            or remaining < 0
            or value["image_text"] != text_by_remaining.get(remaining)
            or content != value
        ):
            raise FileSecurityError("Fake candidate content does not match its JSON file")

    @staticmethod
    def _validate_candidate_image(
        image_data: bytes,
        candidate: ArtifactReference,
    ) -> None:
        with Image.open(BytesIO(image_data)) as image:
            image.verify()
        with Image.open(BytesIO(image_data)) as image:
            formats = {
                "PNG": ("image/png", {".png"}),
                "JPEG": ("image/jpeg", {".jpg", ".jpeg"}),
                "WEBP": ("image/webp", {".webp"}),
            }
            format_info = formats.get(image.format)
            if format_info is None:
                raise FileSecurityError("candidate file must decode as PNG, JPEG, or WebP")
            expected_media_type, expected_suffixes = format_info
            if candidate.media_type != expected_media_type:
                raise FileSecurityError("candidate media type does not match its image format")
            if PurePosixPath(candidate.uri).suffix.lower() not in expected_suffixes:
                raise FileSecurityError("candidate filename extension does not match its image format")
            if image.size != (720, 1280):
                raise FileSecurityError("candidate image must be 720 by 1280 pixels")
            image.load()

    async def _judge(self, state: _WorkflowState) -> dict[str, object]:
        stage = state.get("review_stage")
        candidate = state.get("candidate")
        if stage is None or candidate is None:
            raise AgentIdentityMismatchError("quality review requires a current candidate")
        review = stage.output.review
        if review is None:
            state["session"].memory.set_unresolved_issues(("quality review is missing",))
            return {
                "status": WorkflowStatus.needs_review,
                "stop_reason": "quality review is missing",
                "stop": True,
            }
        if (
            review.candidate_id != candidate.artifact_id
            or review.candidate_revision != candidate.revision
        ):
            raise AgentIdentityMismatchError(
                "quality review does not identify the current candidate version"
            )
        if isinstance(review.details, dict) and review.details.get("qc_contract") == "local-manifest-v1/audit-report-v3":
            from .coordination import judge_qc_review
            return await judge_qc_review(self, state, review)
        expected_ids = {item.keyword_id for item in state["request"].keywords}
        actual_ids = {item.keyword_id for item in review.keyword_results}
        if len(review.keyword_results) != len(expected_ids) or actual_ids != expected_ids:
            state["session"].memory.set_unresolved_issues(
                ("quality review does not cover every original keyword",)
            )
            return {
                "review": review,
                "status": WorkflowStatus.needs_review,
                "stop_reason": "quality review does not cover every original keyword",
                "stop": True,
            }
        keyword_failures = [
            item.keyword_id
            for item in review.keyword_results
            if item.status != KeywordReviewStatus.pass_
        ]
        if review.decision == ReviewDecision.pass_ and not keyword_failures:
            state["session"].memory.set_unresolved_issues(())
            return {
                "review": review,
                "status": WorkflowStatus.passed,
                "stop_reason": "",
                "stop": True,
            }
        if review.decision == ReviewDecision.unknown or any(
            item.status == KeywordReviewStatus.unknown for item in review.keyword_results
        ):
            reason = "quality review contains an unknown result"
            state["session"].memory.set_unresolved_issues((reason,))
            return {
                "review": review,
                "status": WorkflowStatus.needs_review,
                "stop_reason": reason,
                "stop": True,
            }
        return {"review": review, "stop": False}

    async def _route(self, state: _WorkflowState) -> dict[str, object]:
        status = state.get("status")
        review = state.get("review")
        if status is not None:
            return {"next_role": "end"}
        authorized = state.get("repair_authorization")
        route = authorized or review
        if route is None or route.action is None or route.target is None:
            reason = "review did not request a supported revision"
            state["session"].memory.set_unresolved_issues((reason,))
            return {
                "status": WorkflowStatus.needs_review,
                "stop_reason": reason,
                "stop": True,
                "next_role": "end",
            }
        if state["revision_rounds"] >= state["session"].budget.max_revision_rounds:
            reason = "revision round budget is exhausted"
            return {
                "status": WorkflowStatus.needs_review,
                "stop_reason": reason,
                "stop": True,
                "next_role": "end",
            }
        signature = self._review_signature(review)
        signatures = state.get("review_signatures", [])
        if signature in signatures:
            reason = "the same review issue remains after revision"
            state["session"].memory.set_unresolved_issues((reason,))
            return {
                "status": WorkflowStatus.needs_review,
                "stop_reason": reason,
                "stop": True,
                "next_role": "end",
            }
        self._validate_revision_target(state, route.action, route.target)
        self._verify_current_files(state)
        self._record_revision(state, review, authorized)
        return self._begin_revision(state, route, [*signatures, signature])

    @staticmethod
    def _review_signature(review: ReviewRecord) -> str:
        value = {
            "decision": review.decision.value,
            "keywords": sorted(
                (item.keyword_id, item.status.value)
                for item in review.keyword_results
            ),
            "details": review.details,
        }
        if isinstance(review.details, dict) and review.details.get("qc_contract") == "local-manifest-v1/audit-report-v3":
            report = review.details["report"]
            value["details"] = {key: report[key] for key in ("issues", "uncertainties", "repair_hint")}
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _validate_revision_target(
        self,
        state: _WorkflowState,
        action: RevisionAction,
        target: VersionDependency,
    ) -> None:
        expected: ArtifactReference | None
        if action == RevisionAction.revise_brief:
            stage = state.get("brief")
            expected = stage.record_reference if stage is not None else None
        elif action == RevisionAction.revise_layout:
            stage = state.get("layout")
            expected = stage.record_reference if stage is not None else None
        elif action == RevisionAction.regenerate_asset:
            expected = next(
                (
                    reference
                    for reference in state.get("assets", [])
                    if reference.artifact_id == target.artifact_id
                    and reference.revision == target.revision
                ),
                None,
            )
            if expected is not None and expected.purpose != ArtifactPurpose.asset:
                expected = None
        elif action == RevisionAction.rerender:
            expected = state.get("candidate")
        else:
            raise ValueError(f"unsupported revision action: {action}")
        if (
            expected is None
            or expected.artifact_id != target.artifact_id
            or expected.revision != target.revision
        ):
            raise AgentIdentityMismatchError(
                f"revision target is stale or invalid for {action.value}"
            )

    @staticmethod
    def _verify_current_files(state: _WorkflowState) -> None:
        session = state["session"]
        for reference in [*state.get("assets", [])]:
            session.file_store.read(reference)
        candidate = state.get("candidate")
        if candidate is not None:
            session.file_store.read(candidate)

    @staticmethod
    def _record_revision(state: _WorkflowState, review: ReviewRecord, authorization: RepairAuthorization | None = None) -> None:
        unresolved = tuple(
            item.keyword_id
            for item in review.keyword_results
            if item.status != KeywordReviewStatus.pass_
        )
        details = copy.deepcopy(review.details)
        if authorization is not None:
            details["authorization"] = authorization.model_dump(mode="json")
            details["repair_plan"] = {"expected_versions": authorization.expected_versions,
                "constraints": list(authorization.constraints), "preserve": list(authorization.preserve)}
        route = authorization or review
        state["session"].memory.add_revision(
            revision_id=f"revision-{uuid4().hex}",
            role=None,
            changes={
                "action": route.action.value if route.action is not None else None,
                "target": route.target.model_dump(mode="json") if route.target else None,
                "details": details,
                "candidate_id": review.candidate_id,
                "reviewer_identity": (
                    review.reviewer_identity.model_dump(mode="json")
                    if review.reviewer_identity is not None
                    else None
                ),
            },
            unresolved_issues=unresolved or ("quality review failed",),
        )

    def _begin_revision(
        self,
        state: _WorkflowState,
        review: ReviewRecord,
        signatures: list[str],
    ) -> dict[str, object]:
        action = review.action
        target = review.target
        if action is None or target is None:
            raise AgentIdentityMismatchError("revision action and target are required")
        session = state["session"]
        previous_candidate = state.get("candidate")
        previous_assets = list(state.get("assets", []))
        previous_layout = state.get("layout")
        updates: dict[str, object] = {
            "revision_action": action,
            "revision_target": target,
            "repair_authorization": None,
            "previous_candidate": previous_candidate,
            "previous_assets": previous_assets,
            "previous_asset_generation_records": self._generation_records_for_assets(
                state,
                previous_assets,
            ),
            "revision_before_result": self._published_result(state),
            "revision_rounds": state["revision_rounds"] + 1,
            "review_signatures": signatures,
            "candidate": None,
            "visual": None,
            "review_stage": None,
            "review": None,
            "status": None,
            "stop_reason": "",
            "stop": False,
        }
        session.memory.invalidate_artifact("quality_review")
        session.memory.invalidate_artifact("visual_output")
        if action == RevisionAction.revise_brief:
            session.memory.invalidate_artifact("layout")
            session.memory.invalidate_artifact("candidate")
            for reference in previous_assets:
                session.memory.invalidate_artifact(self._memory_asset_name(reference))
            updates.update(
                {
                    "layout": None,
                    "assets": [],
                    "previous_assets": [],
                    "asset_generation_records": [],
                    "previous_asset_generation_records": [],
                    "next_role": AgentRole.creative_director.value,
                }
            )
        elif action == RevisionAction.revise_layout:
            if previous_layout is None:
                raise AgentIdentityMismatchError("revise_layout requires a current layout")
            session.memory.invalidate_artifact("candidate")
            updates.update(
                {
                    "layout": None,
                    "next_role": AgentRole.layout_planner.value,
                }
            )
        elif action == RevisionAction.regenerate_asset:
            session.memory.invalidate_artifact("candidate")
            updates["next_role"] = AgentRole.visual_producer.value
        elif action == RevisionAction.rerender:
            updates["next_role"] = AgentRole.visual_producer.value
        return updates

    @staticmethod
    def _result(state: _WorkflowState, record_path: Path) -> WorkflowResult:
        session = state["session"]
        return WorkflowResult(
            run_id=state["run_id"],
            original_request=state["request"],
            status=state.get("status", WorkflowStatus.needs_review),
            brief=state.get("brief").output if state.get("brief") else None,
            layout=state.get("layout").output if state.get("layout") else None,
            visual=state.get("visual").output if state.get("visual") else None,
            review=state.get("review"),
            candidate=state.get("candidate"),
            assets=tuple(state.get("assets", [])),
            artifacts=session.file_store.artifacts,
            usage=session.usage,
            model_events=session.model_events,
            call_sequence=tuple(state.get("call_sequence", [])),
            revision_rounds=state.get("revision_rounds", 0),
            reason=state.get("stop_reason") or None,
            record_path=record_path,
        )

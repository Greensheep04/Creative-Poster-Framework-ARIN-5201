from __future__ import annotations

import copy
import inspect
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns
from typing import Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter

from .cache import GenerationCache
from .errors import BudgetExceededError, ToolInvocationError
from .files import RunFileStore, ScopedRunFiles
from .generation import ArtifactGenerationRecord
from .memory import MemoryReference, RoleNote, RevisionHistoryRecord, ScopedTaskMemory, TaskMemory
from .tools import ToolDefinition, ToolDescription, ToolRegistry
from .types import (
    AgentRole,
    ArtifactReference,
    ContentConversionRecord,
    ExecutionIdentity,
    FrameworkMetadata,
    JSONValue,
    OriginalRequest,
    RunBudget,
    RunUsage,
)


class ToolCallEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: ExecutionIdentity
    name: str
    arguments: object
    result: object | None = None
    status: Literal["succeeded", "failed", "rejected", "denied"]
    duration_ms: float


class ModelCallEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: ExecutionIdentity
    provider: str
    model: str
    kind: str
    request_data: JSONValue
    parameters: JSONValue
    status: Literal["succeeded", "failed", "rejected", "denied"]
    executed: bool
    duration_ms: float


class RunRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    record_version: Literal[1] = 1
    run_id: str
    original_request: OriginalRequest
    configuration: JSONValue
    artifacts: tuple[ArtifactReference, ...]
    generation_records: tuple[ArtifactGenerationRecord, ...] = ()
    conversion_records: tuple[ContentConversionRecord, ...]
    tool_events: tuple[ToolCallEvent, ...]
    model_events: tuple[ModelCallEvent, ...]
    usage: RunUsage
    budget: RunBudget
    final_result: JSONValue | None
    memory: JSONValue


def _event_value(value: object, key: str | None = None) -> object:
    if key is not None:
        normalized_key = "".join(
            character for character in key.casefold() if character.isalnum()
        )
        sensitive = (
            "apikey",
            "authorization",
            "accesstoken",
            "refreshtoken",
            "token",
            "secret",
            "password",
            "credential",
            "privatekey",
        )
        if normalized_key == "auth" or any(term in normalized_key for term in sensitive):
            return "[REDACTED]"
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    if isinstance(value, Mapping):
        return {str(name): _event_value(item, str(name)) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_event_value(item) for item in value]
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else f"[NON_FINITE:{value!r}]"
    return f"[NON_JSON:{type(value).__name__}]"


class ScopedToolExecutor:
    def __init__(self, session: RunSession, identity: ExecutionIdentity) -> None:
        self._session = session
        self._identity = identity

    async def call(self, name: str, arguments: Mapping[str, object]) -> object:
        return await self._session._invoke_tool(self._identity, name, arguments)

    def available_tools(self) -> tuple[ToolDescription, ...]:
        return self._session.tool_registry.available_tools(self._identity.role)


@dataclass(frozen=True, slots=True)
class RunContext:
    identity: ExecutionIdentity
    _metadata: FrameworkMetadata
    _session: RunSession

    @property
    def metadata(self) -> FrameworkMetadata:
        return self._metadata.model_copy(
            update={
                "usage": self._session.usage,
                "budget": self._session.budget,
            },
            deep=True,
        )

    @property
    def tools(self) -> ScopedToolExecutor:
        return ScopedToolExecutor(self._session, self.identity)

    @property
    def original_request(self) -> OriginalRequest:
        return self._session.original_request

    @property
    def memory(self) -> ScopedTaskMemory:
        return self._session.memory.scope(
            self.identity,
            reference_validator=self._session.file_store.read,
        )

    @property
    def files(self) -> ScopedRunFiles:
        return self._session.file_store.scope(self.identity)

    @property
    def cache(self) -> GenerationCache:
        return self._session.cache

    def build_model_request(
        self,
        messages: Sequence[JSONValue],
        *,
        max_history_chars: int,
    ) -> JSONValue:
        return self._session.build_model_request(
            self.identity,
            messages,
            max_history_chars=max_history_chars,
        )

    async def call_model(
        self,
        *,
        provider: str,
        model: str,
        request_data: JSONValue,
        parameters: JSONValue,
        invoke: Callable[[str, str, JSONValue, JSONValue], object],
        kind: Literal["llm", "image_operation", "critic_vision"] = "llm",
    ) -> object:
        return await self._session._invoke_model(
            self.identity,
            provider=provider,
            model=model,
            kind=kind,
            request_data=request_data,
            parameters=parameters,
            invoke=invoke,
        )


class RunSession:
    def __init__(
        self,
        original_request: OriginalRequest,
        *,
        root: str,
        tool_registry: ToolRegistry | None = None,
        budget: RunBudget | None = None,
        configuration: Mapping[str, object] | None = None,
    ) -> None:
        if not isinstance(original_request, OriginalRequest):
            raise TypeError("original_request must be an OriginalRequest")
        self._original_request = original_request.model_copy(deep=True)
        self.root = root
        self.memory = TaskMemory(self._original_request)
        self.file_store = RunFileStore(root, self._original_request.run_id)
        self.cache = GenerationCache(self.file_store)
        self.tool_registry = (
            tool_registry.snapshot() if tool_registry is not None else ToolRegistry()
        )
        self.budget = (budget or RunBudget()).model_copy(deep=True)
        configuration_value = dict(configuration or {})
        if any(not isinstance(key, str) for key in configuration_value):
            raise TypeError("run configuration keys must be strings")
        self._configuration = copy.deepcopy(
            TypeAdapter(JSONValue).validate_python(configuration_value)
        )
        self._tool_events: list[ToolCallEvent] = []
        self._model_events: list[ModelCallEvent] = []
        self._conversion_records: list[ContentConversionRecord] = []
        self._final_result: JSONValue | None = None
        self._counts = {
            "model_calls": 0,
            "image_operations": 0,
            "critic_vision_calls": 0,
            "tool_calls": 0,
        }

    @property
    def run_id(self) -> str:
        return self._original_request.run_id

    @property
    def original_request(self) -> OriginalRequest:
        return self._original_request.model_copy(deep=True)

    @property
    def usage(self) -> RunUsage:
        return RunUsage(**self._counts)

    @property
    def tool_events(self) -> tuple[ToolCallEvent, ...]:
        return tuple(self._tool_events)

    @property
    def model_events(self) -> tuple[ModelCallEvent, ...]:
        return tuple(self._model_events)

    @property
    def configuration(self) -> JSONValue:
        return copy.deepcopy(self._configuration)

    @property
    def conversion_records(self) -> tuple[ContentConversionRecord, ...]:
        return tuple(item.model_copy(deep=True) for item in self._conversion_records)

    @property
    def final_result(self) -> JSONValue | None:
        return copy.deepcopy(self._final_result)

    def create_context(
        self,
        identity: ExecutionIdentity,
        metadata: FrameworkMetadata | None = None,
    ) -> RunContext:
        if identity.run_id != self.run_id:
            raise ToolInvocationError("execution identity belongs to another run")
        context_metadata = metadata or FrameworkMetadata(
            identity=identity,
            original_request=self.original_request,
            budget=self.budget,
            usage=self.usage,
        )
        if context_metadata.identity != identity:
            raise ToolInvocationError("context metadata identity must match its execution")
        if context_metadata.original_request != self._original_request:
            raise ToolInvocationError("context metadata must use this run's original request")
        return RunContext(
            identity=identity,
            _metadata=context_metadata.model_copy(deep=True),
            _session=self,
        )

    def build_model_request(
        self,
        identity: ExecutionIdentity,
        messages: Sequence[JSONValue],
        *,
        max_history_chars: int,
    ) -> JSONValue:
        if identity.run_id != self.run_id:
            raise ToolInvocationError("model request identity belongs to another run")
        if isinstance(max_history_chars, bool) or not isinstance(max_history_chars, int):
            raise TypeError("max_history_chars must be an integer")
        if max_history_chars < 0:
            raise ValueError("max_history_chars must be non-negative")
        if isinstance(messages, (str, bytes)) or not isinstance(messages, Sequence):
            raise TypeError("messages must be a sequence of JSON values")
        selected: list[JSONValue] = []
        used_chars = 0
        for message in reversed(messages):
            checked = TypeAdapter(JSONValue).validate_python(message)
            encoded = json.dumps(
                checked,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if used_chars + len(encoded) > max_history_chars:
                break
            selected.append(copy.deepcopy(checked))
            used_chars += len(encoded)
        selected.reverse()
        return {
            "original_request": self.original_request.model_dump(mode="json"),
            "context": self.memory.context_for(identity),
            "history": selected,
        }

    def record_conversion(self, record: ContentConversionRecord) -> None:
        if not isinstance(record, ContentConversionRecord):
            raise TypeError("record must be a ContentConversionRecord")
        if record.source_provenance.run_id != self.run_id:
            raise ToolInvocationError("conversion record belongs to another run")
        self._conversion_records.append(record.model_copy(deep=True))

    def set_final_result(self, result: JSONValue) -> None:
        self._final_result = copy.deepcopy(TypeAdapter(JSONValue).validate_python(result))

    def save(self, path: str | Path) -> RunRecord:
        request_data = _event_value(self._original_request.model_dump(mode="json"))
        configuration_data = _event_value(self._configuration)
        artifacts = tuple(
            ArtifactReference.model_validate(_event_value(item.model_dump(mode="json")))
            for item in self.file_store.artifacts
        )
        conversions = tuple(
            ContentConversionRecord.model_validate(
                _event_value(item.model_dump(mode="json"))
            )
            for item in self._conversion_records
        )
        generation_records = tuple(
            ArtifactGenerationRecord.model_validate(
                _event_value(item.model_dump(mode="json"))
            )
            for item in self.file_store.generation_records
        )
        tool_events = tuple(
            ToolCallEvent.model_validate(_event_value(item.model_dump(mode="json")))
            for item in self._tool_events
        )
        model_events = tuple(
            ModelCallEvent.model_validate(_event_value(item.model_dump(mode="json")))
            for item in self._model_events
        )
        record = RunRecord(
            run_id=self.run_id,
            original_request=request_data,
            configuration=configuration_data,
            artifacts=artifacts,
            generation_records=generation_records,
            conversion_records=conversions,
            tool_events=tool_events,
            model_events=model_events,
            usage=self.usage,
            budget=self.budget,
            final_result=_event_value(self._final_result),
            memory=_event_value(self.memory.snapshot()),
        )
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        return record

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        root: str | Path,
        tool_registry: ToolRegistry | None = None,
    ) -> RunSession:
        record = RunRecord.model_validate_json(Path(path).read_text(encoding="utf-8"))
        if record.run_id != record.original_request.run_id:
            raise ValueError("saved run identity does not match its original request")
        session = cls(
            record.original_request,
            root=root,
            tool_registry=tool_registry,
            budget=record.budget,
            configuration=record.configuration,
        )
        session._counts = record.usage.model_dump()
        session._tool_events = [item.model_copy(deep=True) for item in record.tool_events]
        session._model_events = [item.model_copy(deep=True) for item in record.model_events]
        session._conversion_records = [
            item.model_copy(deep=True) for item in record.conversion_records
        ]
        session._final_result = copy.deepcopy(record.final_result)
        session.memory.restore_snapshot(record.memory)
        for artifact in record.artifacts:
            session.file_store.restore_reference(artifact)
        for generation_record in record.generation_records:
            session.file_store.restore_generation_record(generation_record)
        return session

    async def _invoke_tool(
        self,
        identity: ExecutionIdentity,
        name: str,
        arguments: Mapping[str, object],
    ) -> object:
        started = perf_counter_ns()
        safe_arguments = _event_value(arguments)
        try:
            if identity.run_id != self.run_id:
                raise ToolInvocationError("tool identity belongs to another run")
            if not isinstance(arguments, Mapping):
                raise TypeError("tool arguments must be a mapping")
            try:
                definition = self.tool_registry.definition(name)
            except KeyError as error:
                raise ToolInvocationError(str(error)) from error
            if identity.role not in definition.allowed_roles:
                raise ToolInvocationError(
                    f"role {identity.role.value} is not allowed to call tool {name}"
                )
            validated = definition.validate_arguments(arguments)
        except Exception:
            self._append_tool_event(
                identity,
                name,
                safe_arguments,
                None,
                "rejected",
                started,
            )
            raise

        max_calls = self.budget.model_dump().get("max_tool_calls", 100)
        if self._counts["tool_calls"] >= max_calls:
            self._append_tool_event(
                identity,
                name,
                safe_arguments,
                None,
                "denied",
                started,
            )
            raise BudgetExceededError("tool call budget is exhausted")

        self._counts["tool_calls"] += 1
        try:
            result = definition.invoke(validated, self.create_context(identity))
            if inspect.isawaitable(result):
                result = await result
            checked = definition.validate_output(result)
            safe_result = _event_value(checked)
        except Exception:
            self._append_tool_event(
                identity,
                name,
                safe_arguments,
                None,
                "failed",
                started,
            )
            raise
        self._append_tool_event(
            identity,
            name,
            safe_arguments,
            safe_result,
            "succeeded",
            started,
        )
        return checked

    def _append_tool_event(
        self,
        identity: ExecutionIdentity,
        name: str,
        arguments: object,
        result: object | None,
        status: Literal["succeeded", "failed", "rejected", "denied"],
        started: int,
    ) -> None:
        elapsed_ms = (perf_counter_ns() - started) / 1_000_000
        self._tool_events.append(
            ToolCallEvent(
                identity=identity,
                name=name,
                arguments=arguments,
                result=result,
                status=status,
                duration_ms=elapsed_ms,
            )
        )

    async def _invoke_model(
        self,
        identity: ExecutionIdentity,
        *,
        provider: str,
        model: str,
        kind: str,
        request_data: JSONValue,
        parameters: JSONValue,
        invoke: Callable[[str, str, JSONValue, JSONValue], object],
    ) -> object:
        started = perf_counter_ns()
        safe_request = _event_value(request_data)
        safe_parameters = _event_value(parameters)
        if identity.run_id != self.run_id:
            self._append_model_event(
                identity, provider, model, kind, safe_request, safe_parameters,
                "rejected", False, started,
            )
            raise ToolInvocationError("model identity belongs to another run")
        if not isinstance(kind, str) or kind not in {
            "llm",
            "image_operation",
            "critic_vision",
        }:
            event_kind = (
                kind
                if isinstance(kind, str)
                else f"[INVALID_KIND:{type(kind).__name__}]"
            )
            self._append_model_event(
                identity, provider, model, event_kind, safe_request, safe_parameters,
                "rejected", False, started,
            )
            if not isinstance(kind, str):
                raise TypeError("model call kind must be a string")
            raise ToolInvocationError(f"unsupported model call kind: {kind}")
        if (
            not callable(invoke)
            or not isinstance(provider, str)
            or not provider.strip()
            or not isinstance(model, str)
            or not model.strip()
        ):
            self._append_model_event(
                identity, provider, model, kind, safe_request, safe_parameters,
                "rejected", False, started,
            )
            raise TypeError("model provider, model, and invocation function are required")
        try:
            request_value = TypeAdapter(JSONValue).validate_python(request_data)
            parameter_value = TypeAdapter(JSONValue).validate_python(parameters)
        except Exception:
            self._append_model_event(
                identity, provider, model, kind, safe_request, safe_parameters,
                "rejected", False, started,
            )
            raise
        if kind == "critic_vision" and identity.role != AgentRole.quality_critic:
            self._append_model_event(
                identity, provider, model, kind, safe_request, safe_parameters,
                "rejected", False, started,
            )
            raise ToolInvocationError("critic_vision calls require the quality_critic role")
        if kind == "image_operation" and identity.role != AgentRole.visual_producer:
            self._append_model_event(
                identity, provider, model, kind, safe_request, safe_parameters,
                "rejected", False, started,
            )
            raise ToolInvocationError("image_operation calls require the visual_producer role")

        budget = self.budget.model_dump()
        exhausted = self._counts["model_calls"] >= budget["max_model_calls"]
        if kind == "image_operation":
            exhausted = exhausted or self._counts["image_operations"] >= budget[
                "max_image_operations"
            ]
        if kind == "critic_vision":
            exhausted = exhausted or self._counts["critic_vision_calls"] >= budget[
                "max_critic_vision_calls"
            ]
        if exhausted:
            self._append_model_event(
                identity, provider, model, kind, safe_request, safe_parameters,
                "denied", False, started,
            )
            raise BudgetExceededError("model call budget is exhausted")

        self._counts["model_calls"] += 1
        if kind == "image_operation":
            self._counts["image_operations"] += 1
        if kind == "critic_vision":
            self._counts["critic_vision_calls"] += 1
        try:
            result = invoke(provider, model, request_value, parameter_value)
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            self._append_model_event(
                identity, provider, model, kind, safe_request, safe_parameters,
                "failed", True, started,
            )
            raise
        self._append_model_event(
            identity, provider, model, kind, safe_request, safe_parameters,
            "succeeded", True, started,
        )
        return result

    def _append_model_event(
        self,
        identity: ExecutionIdentity,
        provider: str,
        model: str,
        kind: str,
        request_data: object,
        parameters: object,
        status: Literal["succeeded", "failed", "rejected", "denied"],
        executed: bool,
        started: int,
    ) -> None:
        self._model_events.append(
            ModelCallEvent(
                identity=identity,
                provider=provider,
                model=model,
                kind=kind,
                request_data=request_data,
                parameters=parameters,
                status=status,
                executed=executed,
                duration_ms=(perf_counter_ns() - started) / 1_000_000,
            )
        )

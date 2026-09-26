from __future__ import annotations

import copy
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TypeAlias

from pydantic import TypeAdapter

from .errors import (
    AgentIdentityMismatchError,
    AgentRegistrationError,
    AgentSetConfigurationError,
    ContentFormatCompatibilityError,
    InterfaceVersionCompatibilityError,
)
from .types import (
    INTERFACE_VERSION,
    Agent,
    AgentContext,
    AgentInput,
    AgentOutput,
    AgentRole,
    ContentConversionRecord,
    ContentFormat,
    ContentProvenance,
    FrameworkMetadata,
    JSONValue,
)

ContentTransform: TypeAlias = Callable[[JSONValue], JSONValue]
BusinessValidator: TypeAlias = Callable[[JSONValue], None]
InstanceFactory: TypeAlias = Callable[[Mapping[str, JSONValue]], Agent]
_CONFIG_ADAPTER: TypeAdapter[dict[str, JSONValue]] = TypeAdapter(dict[str, JSONValue])
_CONTENT_ADAPTER: TypeAdapter[JSONValue] = TypeAdapter(JSONValue)


@dataclass(frozen=True, slots=True)
class ContentAdapter:
    adapter_id: str
    source_format: ContentFormat
    target_format: ContentFormat
    transform: ContentTransform

    def __post_init__(self) -> None:
        if not self.adapter_id.strip():
            raise AgentRegistrationError("adapter_id must be non-empty")
        if not isinstance(self.source_format, ContentFormat):
            raise AgentRegistrationError("source_format must be a ContentFormat")
        if not isinstance(self.target_format, ContentFormat):
            raise AgentRegistrationError("target_format must be a ContentFormat")
        if not callable(self.transform):
            raise AgentRegistrationError("content transform must be callable")


@dataclass(frozen=True, slots=True)
class PreparedAgentInput:
    inputs: AgentInput
    conversion: ContentConversionRecord | None


@dataclass(frozen=True, slots=True)
class PreparedAgentOutput:
    output: AgentOutput
    metadata: FrameworkMetadata
    conversion: ContentConversionRecord | None


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    output: AgentOutput
    metadata: FrameworkMetadata
    input_conversion: ContentConversionRecord | None
    output_conversion: ContentConversionRecord | None


@dataclass(frozen=True, slots=True)
class AgentRegistration:
    role: AgentRole
    instance_factory: InstanceFactory
    input_formats: frozenset[ContentFormat]
    output_formats: frozenset[ContentFormat]
    input_adapters: tuple[ContentAdapter, ...] = ()
    output_adapters: tuple[ContentAdapter, ...] = ()
    input_validator: BusinessValidator | None = None
    output_validator: BusinessValidator | None = None
    interface_versions: frozenset[str] = frozenset({INTERFACE_VERSION})
    config: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.role, AgentRole):
            raise AgentRegistrationError("role must be an AgentRole")
        if not callable(self.instance_factory):
            raise AgentRegistrationError("instance_factory must be callable")
        if self.input_validator is not None and not callable(self.input_validator):
            raise AgentRegistrationError("input_validator must be callable")
        if self.output_validator is not None and not callable(self.output_validator):
            raise AgentRegistrationError("output_validator must be callable")

        input_formats = frozenset(self.input_formats)
        output_formats = frozenset(self.output_formats)
        input_adapters = tuple(self.input_adapters)
        output_adapters = tuple(self.output_adapters)
        interface_versions = frozenset(self.interface_versions)
        if not input_formats or any(not isinstance(item, ContentFormat) for item in input_formats):
            raise AgentRegistrationError("at least one input ContentFormat is required")
        if not output_formats or any(not isinstance(item, ContentFormat) for item in output_formats):
            raise AgentRegistrationError("at least one output ContentFormat is required")
        if not interface_versions or any(
            not isinstance(item, str) or not item.strip() for item in interface_versions
        ):
            raise AgentRegistrationError("at least one non-empty interface version is required")
        if any(not isinstance(item, ContentAdapter) for item in input_adapters + output_adapters):
            raise AgentRegistrationError("format adapters must be ContentAdapter instances")
        if any(item.target_format not in input_formats for item in input_adapters):
            raise AgentRegistrationError("input adapter targets must be declared input formats")
        if any(item.source_format not in output_formats for item in output_adapters):
            raise AgentRegistrationError("output adapter sources must be declared output formats")
        input_sources = [item.source_format for item in input_adapters]
        if len(input_sources) != len(set(input_sources)):
            raise AgentRegistrationError("each input format may have at most one adapter")
        output_routes = [
            (item.source_format, item.target_format) for item in output_adapters
        ]
        if len(output_routes) != len(set(output_routes)):
            raise AgentRegistrationError("output adapter routes must be unique")
        if not isinstance(self.config, Mapping):
            raise AgentRegistrationError("config must be a JSON object")
        if any(not isinstance(key, str) for key in self.config):
            raise AgentRegistrationError("config object keys must be strings")
        config = _CONFIG_ADAPTER.validate_python(dict(self.config))

        object.__setattr__(self, "input_formats", input_formats)
        object.__setattr__(self, "output_formats", output_formats)
        object.__setattr__(self, "input_adapters", input_adapters)
        object.__setattr__(self, "output_adapters", output_adapters)
        object.__setattr__(self, "interface_versions", interface_versions)
        object.__setattr__(self, "config", copy.deepcopy(config))

    def create_agent(self) -> Agent:
        return self.instance_factory(copy.deepcopy(dict(self.config)))

    def prepare_input(self, inputs: AgentInput) -> PreparedAgentInput:
        return _prepare_input(self, inputs)

    def prepare_output(
        self,
        output: AgentOutput,
        metadata: FrameworkMetadata,
        target_format: ContentFormat | None = None,
    ) -> PreparedAgentOutput:
        return _prepare_output(self, output, metadata, target_format)

    def snapshot(self) -> AgentRegistrationSnapshot:
        configuration_json = json.dumps(
            _CONFIG_ADAPTER.validate_python(dict(self.config)),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return AgentRegistrationSnapshot(
            role=self.role,
            instance_factory=self.instance_factory,
            input_formats=self.input_formats,
            output_formats=self.output_formats,
            input_adapters=self.input_adapters,
            output_adapters=self.output_adapters,
            input_validator=self.input_validator,
            output_validator=self.output_validator,
            interface_versions=self.interface_versions,
            _configuration_json=configuration_json,
        )

    async def run_agent(
        self,
        inputs: AgentInput,
        context: AgentContext,
        *,
        output_format: ContentFormat | None = None,
    ) -> AgentRunResult:
        return await self.snapshot().run_agent(
            inputs,
            context,
            output_format=output_format,
        )


@dataclass(frozen=True, slots=True)
class AgentRegistrationSnapshot:
    role: AgentRole
    instance_factory: InstanceFactory
    input_formats: frozenset[ContentFormat]
    output_formats: frozenset[ContentFormat]
    input_adapters: tuple[ContentAdapter, ...]
    output_adapters: tuple[ContentAdapter, ...]
    input_validator: BusinessValidator | None
    output_validator: BusinessValidator | None
    interface_versions: frozenset[str]
    _configuration_json: str = field(repr=False)

    @property
    def config(self) -> dict[str, JSONValue]:
        return json.loads(self._configuration_json)

    def create_agent(self) -> Agent:
        return self.instance_factory(self.config)

    def prepare_input(self, inputs: AgentInput) -> PreparedAgentInput:
        return _prepare_input(self, inputs)

    def prepare_output(
        self,
        output: AgentOutput,
        metadata: FrameworkMetadata,
        target_format: ContentFormat | None = None,
    ) -> PreparedAgentOutput:
        return _prepare_output(self, output, metadata, target_format)

    async def run_agent(
        self,
        inputs: AgentInput,
        context: AgentContext,
        *,
        output_format: ContentFormat | None = None,
    ) -> AgentRunResult:
        return await _run_registration(
            self,
            inputs,
            context,
            output_format=output_format,
        )


@dataclass(frozen=True, slots=True)
class AgentSetSnapshot:
    registrations: Mapping[AgentRole, AgentRegistrationSnapshot]

    def __post_init__(self) -> None:
        registrations = dict(self.registrations)
        _validate_role_set(registrations)
        object.__setattr__(self, "registrations", MappingProxyType(registrations))

    @property
    def roles(self) -> frozenset[AgentRole]:
        return frozenset(self.registrations)

    def registration_for(self, role: AgentRole) -> AgentRegistrationSnapshot:
        return _registration_for(self.registrations, role)

    def create_agent(self, role: AgentRole) -> Agent:
        return self.registration_for(role).create_agent()

    def prepare_input(self, role: AgentRole, inputs: AgentInput) -> PreparedAgentInput:
        return self.registration_for(role).prepare_input(inputs)

    def prepare_output(
        self,
        role: AgentRole,
        output: AgentOutput,
        metadata: FrameworkMetadata,
        target_format: ContentFormat | None = None,
    ) -> PreparedAgentOutput:
        return self.registration_for(role).prepare_output(output, metadata, target_format)

    async def run_agent(
        self,
        role: AgentRole,
        inputs: AgentInput,
        context: AgentContext,
        *,
        output_format: ContentFormat | None = None,
    ) -> AgentRunResult:
        return await self.registration_for(role).run_agent(
            inputs,
            context,
            output_format=output_format,
        )


class AgentSet:
    def __init__(self, registrations: Iterable[AgentRegistration]) -> None:
        registration_rows = tuple(registrations)
        if any(not isinstance(item, AgentRegistration) for item in registration_rows):
            raise AgentSetConfigurationError(
                "AgentSet requires AgentRegistration instances"
            )
        by_role: dict[AgentRole, AgentRegistration] = {}
        for registration in registration_rows:
            if registration.role in by_role:
                raise AgentSetConfigurationError(
                    f"duplicate registration for role {registration.role.value}"
                )
            by_role[registration.role] = registration
        _validate_role_set(by_role)
        self._registrations: Mapping[AgentRole, AgentRegistration] = MappingProxyType(
            by_role
        )

    @property
    def roles(self) -> frozenset[AgentRole]:
        return frozenset(self._registrations)

    def registration_for(self, role: AgentRole) -> AgentRegistration:
        return _registration_for(self._registrations, role)

    def create_agent(self, role: AgentRole) -> Agent:
        return self.registration_for(role).create_agent()

    def prepare_input(self, role: AgentRole, inputs: AgentInput) -> PreparedAgentInput:
        return self.registration_for(role).prepare_input(inputs)

    def prepare_output(
        self,
        role: AgentRole,
        output: AgentOutput,
        metadata: FrameworkMetadata,
        target_format: ContentFormat | None = None,
    ) -> PreparedAgentOutput:
        return self.registration_for(role).prepare_output(output, metadata, target_format)

    def snapshot(self) -> AgentSetSnapshot:
        registrations = {
            role: registration.snapshot()
            for role, registration in self._registrations.items()
        }
        return AgentSetSnapshot(registrations=registrations)

    async def run_agent(
        self,
        role: AgentRole,
        inputs: AgentInput,
        context: AgentContext,
        *,
        output_format: ContentFormat | None = None,
    ) -> AgentRunResult:
        return await self.snapshot().run_agent(
            role,
            inputs,
            context,
            output_format=output_format,
        )


def _validate_role_set(registrations: Mapping[AgentRole, object]) -> None:
    expected = frozenset(AgentRole)
    actual = frozenset(registrations)
    if actual != expected:
        missing = sorted(role.value for role in expected - actual)
        extra = sorted(str(role) for role in actual - expected)
        raise AgentSetConfigurationError(
            f"AgentSet requires exactly four roles; missing={missing}, extra={extra}"
        )


def _registration_for(
    registrations: Mapping[AgentRole, AgentRegistration | AgentRegistrationSnapshot],
    role: AgentRole,
) -> AgentRegistration | AgentRegistrationSnapshot:
    if not isinstance(role, AgentRole):
        raise AgentSetConfigurationError("role must be an AgentRole")
    try:
        return registrations[role]
    except KeyError as error:
        raise AgentSetConfigurationError(f"role is not registered: {role.value}") from error


def _check_interface_version(
    registration: AgentRegistration | AgentRegistrationSnapshot,
    interface_version: str,
) -> None:
    if interface_version not in registration.interface_versions:
        supported = sorted(registration.interface_versions)
        raise InterfaceVersionCompatibilityError(
            f"interface version {interface_version!r} is unsupported by "
            f"{registration.role.value}; supported={supported}"
        )


def _check_output_format_target(
    registration: AgentRegistration | AgentRegistrationSnapshot,
    target_format: ContentFormat | None,
) -> None:
    if target_format is None:
        return
    if not isinstance(target_format, ContentFormat):
        raise TypeError("output_format must be a ContentFormat")

    reachable_formats = set(registration.output_formats)
    reachable_formats.update(adapter.target_format for adapter in registration.output_adapters)
    if target_format not in reachable_formats:
        reachable = sorted(
            f"{item.name}@{item.version}" for item in reachable_formats
        )
        raise ContentFormatCompatibilityError(
            f"requested output format {target_format.name}@{target_format.version} "
            f"is unreachable; reachable={reachable}"
        )


def _validate_business_content(
    validator: BusinessValidator | None,
    content: JSONValue,
) -> None:
    if validator is not None:
        validator(copy.deepcopy(content))


def _input_provenance(inputs: AgentInput) -> ContentProvenance:
    return inputs.provenance or ContentProvenance(
        run_id=inputs.metadata.identity.run_id,
        source_id=inputs.metadata.original_request.request_id,
    )


def _prepare_input(
    registration: AgentRegistration | AgentRegistrationSnapshot,
    inputs: AgentInput,
) -> PreparedAgentInput:
    if not isinstance(inputs, AgentInput):
        raise TypeError("inputs must be AgentInput")
    if (
        inputs.provenance is not None
        and inputs.provenance.run_id != inputs.metadata.identity.run_id
    ):
        raise AgentIdentityMismatchError(
            "input provenance run_id must match the current execution run_id"
        )
    _check_interface_version(registration, inputs.metadata.interface_version)
    if inputs.metadata.identity.role != registration.role:
        raise AgentIdentityMismatchError("input role does not match its registration")
    if inputs.content_format in registration.input_formats:
        _validate_business_content(
            registration.input_validator,
            copy.deepcopy(inputs.content),
        )
        return PreparedAgentInput(inputs=inputs.model_copy(deep=True), conversion=None)

    adapter = next(
        (
            item
            for item in registration.input_adapters
            if item.source_format == inputs.content_format
        ),
        None,
    )
    if adapter is None:
        accepted = sorted(
            f"{item.name}@{item.version}" for item in registration.input_formats
        )
        raise ContentFormatCompatibilityError(
            f"input format {inputs.content_format.name}@{inputs.content_format.version} "
            f"is unsupported by {registration.role.value}; accepted={accepted}"
        )

    source_content = copy.deepcopy(inputs.content)
    converted_content = adapter.transform(copy.deepcopy(source_content))
    converted_content = _CONTENT_ADAPTER.validate_python(converted_content)
    prepared_inputs = inputs.model_copy(
        deep=True,
        update={
            "content": converted_content,
            "content_format": adapter.target_format,
        }
    )
    _validate_business_content(registration.input_validator, prepared_inputs.content)
    record = ContentConversionRecord(
        adapter_id=adapter.adapter_id,
        source_content=source_content,
        source_format=inputs.content_format,
        target_content=copy.deepcopy(prepared_inputs.content),
        target_format=adapter.target_format,
        source_provenance=_input_provenance(inputs),
    )
    return PreparedAgentInput(inputs=prepared_inputs, conversion=record)


def _prepared_output_provenance(metadata: FrameworkMetadata) -> ContentProvenance:
    return ContentProvenance(
        run_id=metadata.identity.run_id,
        source_id=metadata.identity.invocation_id,
        role=metadata.identity.role,
        invocation_id=metadata.identity.invocation_id,
    )


def _prepare_output(
    registration: AgentRegistration | AgentRegistrationSnapshot,
    output: AgentOutput,
    metadata: FrameworkMetadata,
    target_format: ContentFormat | None,
) -> PreparedAgentOutput:
    if not isinstance(output, AgentOutput):
        raise TypeError("output must be AgentOutput")
    if not isinstance(metadata, FrameworkMetadata):
        raise TypeError("metadata must be FrameworkMetadata")
    _check_interface_version(registration, metadata.interface_version)
    if metadata.identity.role != registration.role:
        raise AgentIdentityMismatchError("output role does not match its registration")
    if output.review is not None:
        if metadata.identity.role != AgentRole.quality_critic:
            raise AgentIdentityMismatchError("only quality_critic may return review data")
        reviewer_identity = output.review.reviewer_identity
        if reviewer_identity is not None and reviewer_identity != metadata.identity:
            raise AgentIdentityMismatchError("review identity does not match its invocation")
        if reviewer_identity is None:
            review = output.review.model_copy(
                update={"reviewer_identity": metadata.identity}
            )
            output = output.model_copy(update={"review": review})
    if output.content_format not in registration.output_formats:
        declared = sorted(
            f"{item.name}@{item.version}" for item in registration.output_formats
        )
        raise ContentFormatCompatibilityError(
            f"output format {output.content_format.name}@{output.content_format.version} "
            f"was not declared by {registration.role.value}; declared={declared}"
        )

    target_format = target_format or output.content_format
    if target_format == output.content_format:
        _validate_business_content(registration.output_validator, output.content)
        return PreparedAgentOutput(
            output=output,
            metadata=metadata,
            conversion=None,
        )

    adapter = next(
        (
            item
            for item in registration.output_adapters
            if item.source_format == output.content_format
            and item.target_format == target_format
        ),
        None,
    )
    if adapter is None:
        raise ContentFormatCompatibilityError(
            f"no output adapter converts {output.content_format.name}@"
            f"{output.content_format.version} to {target_format.name}@{target_format.version}"
        )

    source_content = copy.deepcopy(output.content)
    converted_content = adapter.transform(copy.deepcopy(source_content))
    converted_content = _CONTENT_ADAPTER.validate_python(converted_content)
    prepared_output = output.model_copy(
        update={
            "content": converted_content,
            "content_format": adapter.target_format,
        }
    )
    _validate_business_content(registration.output_validator, prepared_output.content)
    record = ContentConversionRecord(
        adapter_id=adapter.adapter_id,
        source_content=source_content,
        source_format=output.content_format,
        target_content=copy.deepcopy(prepared_output.content),
        target_format=adapter.target_format,
        source_provenance=_prepared_output_provenance(metadata),
    )
    return PreparedAgentOutput(
        output=prepared_output,
        metadata=metadata,
        conversion=record,
    )


def _validate_call_identity(
    role: AgentRole,
    inputs: AgentInput,
    context: AgentContext,
) -> None:
    identity = inputs.metadata.identity
    if identity.role != role or context.identity != identity:
        raise AgentIdentityMismatchError(
            "input and context identities must match the registered role and invocation"
        )


async def _run_registration(
    registration: AgentRegistration | AgentRegistrationSnapshot,
    inputs: AgentInput,
    context: AgentContext,
    *,
    output_format: ContentFormat | None = None,
) -> AgentRunResult:
    _check_output_format_target(registration, output_format)
    prepared_input = registration.prepare_input(inputs)
    _validate_call_identity(registration.role, inputs, context)
    agent = registration.create_agent()
    output = await agent.run(prepared_input.inputs, context)
    if not isinstance(output, AgentOutput):
        raise TypeError("Agent.run must return AgentOutput")
    prepared_output = registration.prepare_output(
        output,
        inputs.metadata,
        output_format,
    )
    return AgentRunResult(
        output=prepared_output.output,
        metadata=prepared_output.metadata,
        input_conversion=prepared_input.conversion,
        output_conversion=prepared_output.conversion,
    )

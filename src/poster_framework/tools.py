from __future__ import annotations

import inspect
from copy import deepcopy
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import get_type_hints

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, create_model

from .errors import ToolRegistrationError
from .types import AgentContext, AgentRole, JSONValue


class ToolDescription(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    parameters: dict[str, JSONValue]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    allowed_roles: frozenset[AgentRole]
    function: Callable[..., object]
    input_model: type[BaseModel]
    pass_model: bool
    inject_context: bool
    output_adapter: TypeAdapter[object]

    def parameter_schema(self) -> dict[str, object]:
        return self.input_model.model_json_schema()

    def validate_arguments(self, arguments: Mapping[str, object]) -> BaseModel:
        return self.input_model.model_validate(dict(arguments))

    def invoke(self, arguments: BaseModel, context: AgentContext) -> object:
        if self.pass_model:
            if self.inject_context:
                return self.function(arguments, context=context)
            return self.function(arguments)
        values = {
            name: getattr(arguments, name)
            for name in self.input_model.model_fields
        }
        if self.inject_context:
            values["context"] = context
        return self.function(**values)

    def validate_output(self, value: object) -> object:
        return self.output_adapter.validate_python(value, strict=True)


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def snapshot(self) -> ToolRegistry:
        snapshot = ToolRegistry()
        snapshot._definitions = self._definitions.copy()
        return snapshot

    def register(
        self,
        name: str,
        function: Callable[..., object],
        *,
        description: str,
        allowed_roles: Iterable[AgentRole],
        input_model: type[BaseModel] | None = None,
        output_type: object | None = None,
    ) -> ToolDefinition:
        if not isinstance(name, str) or not name.strip() or "." not in name:
            raise ToolRegistrationError("tool name must be a non-empty namespaced name")
        if name in self._definitions:
            raise ToolRegistrationError(f"tool name is already registered: {name}")
        if not isinstance(description, str) or not description.strip():
            raise ToolRegistrationError("tool description must be non-empty")
        if not callable(function):
            raise ToolRegistrationError("tool function must be callable")
        roles = frozenset(allowed_roles)
        if not roles or any(not isinstance(role, AgentRole) for role in roles):
            raise ToolRegistrationError("tool requires at least one allowed AgentRole")

        hints = (
            get_type_hints(function)
            if input_model is None or output_type is None
            else {}
        )
        signature = inspect.signature(function)
        pass_model = input_model is not None
        inject_context = (
            "context" in signature.parameters
            and hints.get("context", signature.parameters["context"].annotation)
            is AgentContext
        )
        if input_model is None:
            fields: dict[str, tuple[object, object]] = {}
            for parameter in signature.parameters.values():
                if parameter.name == "context" and inject_context:
                    continue
                if parameter.kind not in (
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                ):
                    raise ToolRegistrationError("tool parameters must be named arguments")
                annotation = hints.get(parameter.name, parameter.annotation)
                if annotation is inspect.Parameter.empty:
                    raise ToolRegistrationError(
                        f"tool parameter requires a type annotation: {parameter.name}"
                    )
                default = (
                    ...
                    if parameter.default is inspect.Parameter.empty
                    else parameter.default
                )
                fields[parameter.name] = (annotation, default)
            input_model = create_model(
                f"{name.replace('.', '_').title()}Input",
                __config__=ConfigDict(extra="forbid", strict=True),
                **fields,
            )
        elif not isinstance(input_model, type) or not issubclass(input_model, BaseModel):
            raise ToolRegistrationError("input_model must be a Pydantic BaseModel")

        result_type = output_type if output_type is not None else hints.get(
            "return", signature.return_annotation
        )
        if result_type is inspect.Signature.empty:
            raise ToolRegistrationError("tool return type requires an annotation")

        definition = ToolDefinition(
            name=name,
            description=description.strip(),
            allowed_roles=roles,
            function=function,
            input_model=input_model,
            pass_model=pass_model,
            inject_context=inject_context,
            output_adapter=TypeAdapter(result_type),
        )
        self._definitions[name] = definition
        return definition

    def tool(
        self,
        *,
        name: str,
        description: str,
        allowed_roles: Iterable[AgentRole],
        input_model: type[BaseModel] | None = None,
        output_type: object | None = None,
    ) -> Callable[[Callable[..., object]], Callable[..., object]]:
        def decorate(function: Callable[..., object]) -> Callable[..., object]:
            self.register(
                name,
                function,
                description=description,
                allowed_roles=allowed_roles,
                input_model=input_model,
                output_type=output_type,
            )
            return function

        return decorate

    def definition(self, name: str) -> ToolDefinition:
        try:
            return self._definitions[name]
        except KeyError as error:
            raise KeyError(f"unknown tool: {name}") from error

    def parameter_schema(self, name: str) -> dict[str, object]:
        return self.definition(name).parameter_schema()

    def available_tools(self, role: AgentRole) -> tuple[ToolDescription, ...]:
        if not isinstance(role, AgentRole):
            raise TypeError("role must be an AgentRole")
        return tuple(
            ToolDescription(
                name=definition.name,
                description=definition.description,
                parameters=deepcopy(definition.parameter_schema()),
            )
            for definition in sorted(
                self._definitions.values(),
                key=lambda item: item.name,
            )
            if role in definition.allowed_roles
        )

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from poster_framework import (
    Agent,
    AgentContext,
    AgentInput,
    AgentOutput,
    AgentRegistration,
    AgentRole,
    AgentSet,
    ExecutionMode,
    FakePosterModel,
    PosterWorkflow,
    RunBudget,
    ToolRegistry,
)


def repeat_label(label: str, repeat: int = 2) -> str:
    return " ".join([label] * repeat)


class ToolAwareLayoutAgent(Agent):
    def __init__(self, delegate: Agent) -> None:
        self._delegate = delegate
        self.used_tool = ""
        self.tool_result = ""

    async def run(self, inputs: AgentInput, context: AgentContext) -> AgentOutput:
        descriptions = context.tools.available_tools()
        if [item.name for item in descriptions] != ["layout.repeat_label"]:
            raise AssertionError("layout role did not receive its filtered tool catalog")
        selected = descriptions[0]
        if selected.description != "Repeat a label in the layout." or not selected.parameters:
            raise AssertionError("tool description is missing its purpose or parameters")
        self.used_tool = selected.name
        self.tool_result = await context.tools.call(
            selected.name,
            {"label": "grid", "repeat": 2},
        )
        if self.tool_result != "grid grid":
            raise AssertionError("tool returned an unexpected result")
        return await self._delegate.run(inputs, context)


async def run_example(root: Path) -> dict[str, object]:
    model = FakePosterModel(initial_remaining_revisions=0, delay_seconds=0)
    registry = ToolRegistry()
    registry.register(
        "layout.repeat_label",
        repeat_label,
        description="Repeat a label in the layout.",
        allowed_roles={AgentRole.layout_planner},
    )
    base_registration = model.registration(AgentRole.layout_planner)
    tool_agent = ToolAwareLayoutAgent(base_registration.create_agent())
    tool_registration = AgentRegistration(
        role=base_registration.role,
        instance_factory=lambda config: tool_agent,
        input_formats=base_registration.input_formats,
        output_formats=base_registration.output_formats,
        input_adapters=base_registration.input_adapters,
        output_adapters=base_registration.output_adapters,
        input_validator=base_registration.input_validator,
        output_validator=base_registration.output_validator,
        interface_versions=base_registration.interface_versions,
        config=base_registration.config,
    )
    registrations = [
        model.registration(role)
        for role in AgentRole
        if role != AgentRole.layout_planner
    ]
    registrations.append(tool_registration)
    workflow = PosterWorkflow(
        AgentSet(registrations),
        root=root,
        tool_registry=registry,
        budget=RunBudget(max_revision_rounds=0),
    )
    result = await workflow.run(
        ["green tea", "caffeine-free"],
        constraints={"tone": "calm"},
        execution_mode=ExecutionMode.fake,
    )
    if result.status.value != "passed":
        raise AssertionError("tool example workflow did not pass")
    if result.usage.tool_calls != 1 or tool_agent.used_tool != "layout.repeat_label":
        raise AssertionError("tool use was not recorded for the current run")
    return {
        "run_id": result.original_request.run_id,
        "execution_mode": ExecutionMode.fake.value,
        "role": AgentRole.layout_planner.value,
        "tool": tool_agent.used_tool,
        "tool_result": tool_agent.tool_result,
        "tool_calls": result.usage.tool_calls,
        "status": result.status.value,
        "record_path": str(result.record_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(".scratch/tool-catalog"))
    arguments = parser.parse_args()
    result = asyncio.run(run_example(arguments.root))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()

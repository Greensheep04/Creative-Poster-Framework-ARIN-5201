from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from poster_framework import (
    AgentInput, AgentRole, ContentFormat, ExecutionIdentity, ExecutionMode,
    FrameworkMetadata, OriginalRequest,
)
from poster_framework.layout_planner import (
    LAYOUT_BRIEF_FORMAT, LayoutPlannerAgent, POSTER_SPEC_FORMAT, validate_poster_spec,
)
from poster_framework import AgentRegistration, AgentRole


def registration() -> AgentRegistration:
    return AgentRegistration(
        role=AgentRole.layout_planner,
        instance_factory=lambda config: LayoutPlannerAgent(),
        input_formats=frozenset({LAYOUT_BRIEF_FORMAT}),
        output_formats=frozenset({POSTER_SPEC_FORMAT}),
        output_validator=validate_poster_spec,
    )


async def run_example(root: Path) -> dict[str, object]:
    request = OriginalRequest.from_keywords("layout-planner-example", ["海洋", "宁静"])
    brief = {"copy": {"title": "静海之蓝", "subtitle": "留一点宁静给自己"}}
    metadata = FrameworkMetadata(
        identity=ExecutionIdentity(
            run_id=request.run_id,
            role=AgentRole.layout_planner,
            invocation_id="layout-planner-example-invocation",
            execution_mode=ExecutionMode.real,
        ),
        original_request=request,
    )
    inputs = AgentInput(content=brief, content_format=LAYOUT_BRIEF_FORMAT, metadata=metadata)

    class Context:
        original_request = request

    output = await LayoutPlannerAgent().run(inputs, Context())
    validate_poster_spec(output.content)
    return {
        "run_id": request.run_id,
        "execution_mode": ExecutionMode.real.value,
        "output_format": output.content_format.model_dump(mode="json"),
        "poster_spec": output.content,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(".scratch/layout-planner"))
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run_example(args.root)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

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
    ContentAdapter,
    ContentFormat,
    OriginalRequest,
    WORKFLOW_REQUEST_FORMAT,
    run_single_agent_from_request,
    run_single_agent_from_upstream,
)


BRIEF_V1 = ContentFormat(name="poster.brief", version="1")
BRIEF_V2 = ContentFormat(name="poster.brief", version="2")
LAYOUT_V1 = ContentFormat(name="poster.layout", version="1")


def validate_brief_v1(content: object) -> None:
    if not isinstance(content, dict):
        raise ValueError("v1 brief must be a JSON object")
    if not isinstance(content.get("headline"), str) or not content["headline"].strip():
        raise ValueError("v1 brief headline must be non-empty")
    if not isinstance(content.get("tone"), str) or not content["tone"].strip():
        raise ValueError("v1 brief tone must be non-empty")


def adapt_brief_v1_to_v2(content: object) -> object:
    validate_brief_v1(content)
    return {
        "title": content["headline"],
        "visual_tone": content["tone"],
        "format_version": "2",
    }


def validate_brief_v2(content: object) -> None:
    if not isinstance(content, dict):
        raise ValueError("v2 brief must be a JSON object")
    if not isinstance(content.get("title"), str) or not content["title"].strip():
        raise ValueError("v2 brief title must be non-empty")
    if not isinstance(content.get("visual_tone"), str) or not content["visual_tone"].strip():
        raise ValueError("v2 brief visual_tone must be non-empty")
    if content.get("format_version") != "2":
        raise ValueError("v2 brief format_version must equal 2")


def validate_layout(content: object) -> None:
    if not isinstance(content, dict):
        raise ValueError("layout must be a JSON object")
    if not isinstance(content.get("title"), str) or not content["title"].strip():
        raise ValueError("layout title must be non-empty")
    if not isinstance(content.get("visual_tone"), str) or not content["visual_tone"].strip():
        raise ValueError("layout visual_tone must be non-empty")


class BriefV1Agent(Agent):
    async def run(self, inputs: AgentInput, context: AgentContext) -> AgentOutput:
        if inputs.content_format != WORKFLOW_REQUEST_FORMAT:
            raise ValueError("BriefV1Agent requires a workflow request")
        request = context.original_request
        content = {
            "headline": request.keywords[0].text,
            "tone": "calm",
        }
        validate_brief_v1(content)
        return AgentOutput(content=content, content_format=BRIEF_V1)


class LayoutV1Agent(Agent):
    def __init__(self) -> None:
        self.received_content: object | None = None
        self.received_format: ContentFormat | None = None

    async def run(self, inputs: AgentInput, context: AgentContext) -> AgentOutput:
        self.received_content = inputs.content
        self.received_format = inputs.content_format
        validate_brief_v2(inputs.content)
        content = {
            "title": inputs.content["title"],
            "visual_tone": inputs.content["visual_tone"],
            "columns": 3,
        }
        validate_layout(content)
        return AgentOutput(content=content, content_format=LAYOUT_V1)


async def run_example(root: Path) -> dict[str, object]:
    sample_path = Path(__file__).with_name("sample-request.json")
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    request = OriginalRequest.from_keywords(
        "example-format-upgrade",
        sample["keywords"],
        sample["constraints"],
    )
    source_registration = AgentRegistration(
        role=AgentRole.creative_director,
        instance_factory=lambda config: BriefV1Agent(),
        input_formats=frozenset({WORKFLOW_REQUEST_FORMAT}),
        output_formats=frozenset({BRIEF_V1}),
        output_validator=validate_brief_v1,
    )
    source_result = await run_single_agent_from_request(
        source_registration,
        request,
        root=root,
    )
    adapter = ContentAdapter(
        adapter_id="poster-brief-v1-to-v2",
        source_format=BRIEF_V1,
        target_format=BRIEF_V2,
        transform=adapt_brief_v1_to_v2,
    )
    layout_agent = LayoutV1Agent()
    layout_registration = AgentRegistration(
        role=AgentRole.layout_planner,
        instance_factory=lambda config: layout_agent,
        input_formats=frozenset({BRIEF_V2}),
        output_formats=frozenset({LAYOUT_V1}),
        input_adapters=(adapter,),
        input_validator=validate_brief_v2,
        output_validator=validate_layout,
    )
    downstream_record_path = (
        root / "runs" / request.run_id / "layout-run-record.json"
    )
    downstream_result = await run_single_agent_from_upstream(
        layout_registration,
        source_result.record_path,
        root=root,
        record_path=downstream_record_path,
    )
    conversion = downstream_result.agent_result.input_conversion
    if conversion is None or conversion.adapter_id != adapter.adapter_id:
        raise AssertionError("registered format adapter did not run")
    expected_content = {
        "title": source_result.agent_result.output.content["headline"],
        "visual_tone": source_result.agent_result.output.content["tone"],
        "format_version": "2",
    }
    if layout_agent.received_content != expected_content:
        raise AssertionError("downstream Agent did not consume converted v2 content")
    if layout_agent.received_format != BRIEF_V2:
        raise AssertionError("downstream Agent received the wrong content format")
    validate_layout(downstream_result.agent_result.output.content)
    return {
        "run_id": request.run_id,
        "source_format": source_result.agent_result.output.content_format.model_dump(
            mode="json"
        ),
        "conversion": conversion.model_dump(mode="json"),
        "downstream_input": layout_agent.received_content,
        "downstream_output": downstream_result.agent_result.output.model_dump(
            mode="json"
        ),
        "source_record_path": str(source_result.record_path),
        "downstream_record_path": str(downstream_result.record_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(".scratch/format-upgrade"))
    arguments = parser.parse_args()
    result = asyncio.run(run_example(arguments.root))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()

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
    ContentFormat,
    ExecutionMode,
    OriginalRequest,
    WORKFLOW_REQUEST_FORMAT,
    run_single_agent_from_request,
)


BRIEF_FORMAT = ContentFormat(name="poster.creative-brief", version="1")


def validate_brief(content: object) -> None:
    if not isinstance(content, dict):
        raise ValueError("brief content must be a JSON object")
    headline = content.get("headline")
    keywords = content.get("keywords")
    tone = content.get("tone")
    if not isinstance(headline, str) or not headline.strip():
        raise ValueError("brief headline must be non-empty")
    if not isinstance(keywords, list) or not keywords:
        raise ValueError("brief keywords must be a non-empty list")
    if any(not isinstance(item, str) or not item.strip() for item in keywords):
        raise ValueError("brief keywords must contain non-empty strings")
    if not isinstance(tone, str) or not tone.strip():
        raise ValueError("brief tone must be non-empty")


class CreativeBriefAgent(Agent):
    async def run(self, inputs: AgentInput, context: AgentContext) -> AgentOutput:
        if inputs.content_format != WORKFLOW_REQUEST_FORMAT:
            raise ValueError("CreativeBriefAgent requires a workflow request")
        request = context.original_request
        tone = next(
            (item.value for item in request.constraints if item.name == "tone"),
            "balanced",
        )
        content = {
            "headline": request.keywords[0].text,
            "keywords": [item.text for item in request.keywords],
            "tone": tone,
        }
        validate_brief(content)
        return AgentOutput(content=content, content_format=BRIEF_FORMAT)


def registration() -> AgentRegistration:
    return AgentRegistration(
        role=AgentRole.creative_director,
        instance_factory=lambda config: CreativeBriefAgent(),
        input_formats=frozenset({WORKFLOW_REQUEST_FORMAT}),
        output_formats=frozenset({BRIEF_FORMAT}),
        output_validator=validate_brief,
    )


async def run_example(root: Path) -> dict[str, object]:
    sample_path = Path(__file__).with_name("sample-request.json")
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    request = OriginalRequest.from_keywords(
        "example-single-role",
        sample["keywords"],
        sample["constraints"],
    )
    result = await run_single_agent_from_request(
        registration(),
        request,
        root=root,
    )
    output_data = result.agent_result.output.model_dump(mode="json")
    public_fields = {"content", "content_format", "artifacts", "review"}
    if set(output_data) != public_fields:
        raise AssertionError("AgentOutput public fields do not match the interface")
    if result.metadata.identity.execution_mode != ExecutionMode.real:
        raise AssertionError("single-role sample must use the default real mode")
    validate_brief(output_data["content"])
    return {
        "run_id": result.metadata.identity.run_id,
        "execution_mode": result.metadata.identity.execution_mode.value,
        "output": output_data,
        "record_path": str(result.record_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(".scratch/single-role"))
    arguments = parser.parse_args()
    result = asyncio.run(run_example(arguments.root))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()

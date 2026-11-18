from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
from pydantic import SecretStr

import httpx

from creative.prompt_loader import load_prompt, load_revision_prompt

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
    RevisionAction,
    WORKFLOW_REQUEST_FORMAT,
    run_single_agent_from_request,
)


BRIEF_FORMAT = ContentFormat(name="poster.creative-brief", version="2")
DEFAULT_HF_MODEL = "Qwen/Qwen3-4B-Instruct-2507"
# These small Qwen models are served by the provider-specific HF route.
HF_CHAT_URL = "https://router.huggingface.co/featherless-ai/v1/chat/completions"


def validate_brief(content: object) -> None:
    """Validate the detailed Creative Director JSON brief."""

    def check_fields(
        value: object,
        path: str,
        *,
        text_fields: tuple[str, ...],
        list_fields: tuple[str, ...] = (),
        object_fields: tuple[str, ...] = (),
    ) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be a JSON object")
        expected = set(text_fields + list_fields + object_fields)
        for field in sorted(expected - value.keys()):
            raise ValueError(f"{path}.{field} is required")
        for field in sorted(value.keys() - expected, key=str):
            raise ValueError(f"{path}.{field} is not an allowed field")

        for field in text_fields:
            field_path = f"{path}.{field}"
            item = value[field]
            if not isinstance(item, str):
                raise ValueError(f"{field_path} must be a string")
            optional_copy = field_path in {
                "brief.copy.subtitle", "brief.copy.call_to_action"
            }
            if not item.strip() and not (optional_copy and item == ""):
                raise ValueError(f"{field_path} must be non-empty text")

        for field in list_fields:
            field_path = f"{path}.{field}"
            items = value[field]
            if not isinstance(items, list):
                raise ValueError(f"{field_path} must be a list of strings")
            if field_path in {"brief.keywords", "brief.palette.colors"} and not items:
                raise ValueError(f"{field_path} must be a non-empty list")
            for index, item in enumerate(items):
                if not isinstance(item, str) or not item.strip():
                    raise ValueError(f"{field_path}[{index}] must be non-empty text")
        return value

    content = check_fields(
        content,
        "brief",
        text_fields=("interpretation", "audience", "objective", "core_message"),
        list_fields=("keywords", "avoid"),
        object_fields=("concept", "copy", "visual_direction", "layout", "palette"),
    )
    check_fields(
        content["concept"], "brief.concept",
        text_fields=("title", "central_idea", "connection_to_keywords"),
    )
    check_fields(
        content["copy"], "brief.copy",
        text_fields=("headline", "subtitle", "call_to_action"),
    )
    check_fields(
        content["visual_direction"], "brief.visual_direction",
        text_fields=("main_subject", "setting", "visual_relationship", "style", "mood"),
        list_fields=("supporting_elements",),
    )
    check_fields(
        content["layout"], "brief.layout",
        text_fields=("focal_point", "composition", "reading_order", "typography"),
    )
    check_fields(
        content["palette"], "brief.palette",
        text_fields=("rationale",), list_fields=("colors",),
    )


def provider_error_detail(response: httpx.Response, token: str) -> str:
    """Keep the provider's diagnostic message while removing credentials."""
    try:
        payload = response.json()
    except ValueError:
        detail = response.text
    else:
        detail = ""
        if isinstance(payload, dict):
            detail = payload.get("error") or payload.get("message") or payload.get("detail") or ""
            if isinstance(detail, dict):
                detail = detail.get("message", "")
        if not isinstance(detail, str):
            detail = ""
    if token:
        detail = detail.replace(token, "[REDACTED]")
    detail = re.sub(r"\bhf_[A-Za-z0-9_-]+", "[REDACTED]", detail)
    detail = re.sub(r"(?i)\bBearer\s+[^\s\"'<>,;]+", "Bearer [REDACTED]", detail)
    return " ".join(detail.split())[:800]


async def call_hugging_face(
    provider: str, model: str, request_data: dict, parameters: dict,
    *, api_key: str | None = None, endpoint: str = HF_CHAT_URL, timeout_seconds: float = 120.0,
) -> str:
    """Make one request; the token stays outside the framework's logged data."""
    token = (api_key if api_key is not None else os.environ.get("HF_TOKEN", "")).strip()
    if not token:
        raise RuntimeError(
            "HF_TOKEN is not set. Add it to your PyCharm run configuration's "
            "environment variables before running the creative director."
        )
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                endpoint,
                headers={"Authorization": f"Bearer {token}"},
                json={"model": model, **request_data, **parameters},
            )
    except httpx.TimeoutException:
        raise RuntimeError(f"Hugging Face request timed out after {timeout_seconds:g} seconds.") from None
    except httpx.RequestError:
        raise RuntimeError("Could not reach Hugging Face. Check your network connection.") from None

    if not response.is_success:
        hints = {
            400: "The provider rejected the request. Check HF_MODEL and the request parameters.",
            401: "Check that HF_TOKEN is a valid Hugging Face access token.",
            402: "Check your Hugging Face inference credits or billing.",
            403: "HF_TOKEN needs permission to make calls to Inference Providers.",
            404: "Check that HF_MODEL is available through Inference Providers.",
            429: "The provider rate limit was reached; try again later.",
        }
        hint = hints.get(response.status_code, "The provider could not complete the request; try again later.")
        detail = provider_error_detail(response, token)
        raise RuntimeError(f"Hugging Face HTTP {response.status_code} for {model}. {detail or hint}")

    try:
        payload = response.json()
        choice = payload["choices"][0]
        finish_reason = choice.get("finish_reason")
        text = choice["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise ValueError("Hugging Face returned an invalid chat completion response.") from None
    if finish_reason == "length":
        raise ValueError("Hugging Face truncated the brief at the output token limit.")
    if finish_reason not in (None, "stop"):
        raise ValueError("Hugging Face did not return a completed text response.")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Hugging Face returned no creative brief text.")
    return text


def _revision_data(context: AgentContext) -> dict[str, object]:
    """Read the prior brief and feedback for the workflow's revision target."""
    target = context.metadata.revision_target
    if target is None:
        raise ValueError("Brief revision requires a revision target")
    memory = context.memory.context()
    previous = next(
        (
            item for item in memory["artifacts"]
            if item["name"] == "brief"
            and item["reference"]["artifact_id"] == target.artifact_id
            and item["reference"]["revision"] == target.revision
        ),
        None,
    )
    if previous is None:
        raise ValueError("Brief revision requires the previous brief matching the revision target")
    validate_brief(previous["content"])
    target_data = target.model_dump(mode="json")
    feedback = next(
        (
            item for item in reversed(memory["revision_history"])
            if isinstance(item["changes"], dict)
            and item["changes"].get("action") == RevisionAction.revise_brief.value
            and item["changes"].get("target") == target_data
        ),
        None,
    )
    if feedback is None:
        raise ValueError("Brief revision requires critic feedback matching the revision target")
    return {
        "previous_brief": previous["content"],
        "feedback": feedback["changes"].get("details"),
        "unresolved_issues": feedback["unresolved_issues"],
    }


class CreativeDirectorAgent(Agent):
    def __init__(self, model: str | None = None, *, api_key: str | None = None,
                 endpoint: str = HF_CHAT_URL, timeout_seconds: float = 120.0,
                 max_tokens: int = 2048, temperature: float = 0.7):
        self.model = model or os.environ.get("HF_MODEL", "").strip() or DEFAULT_HF_MODEL
        self._api_key = SecretStr(api_key) if api_key is not None else None
        self.endpoint, self.timeout_seconds = endpoint, timeout_seconds
        self.max_tokens, self.temperature = max_tokens, temperature

    async def run(self, inputs: AgentInput, context: AgentContext) -> AgentOutput:
        if inputs.content_format != WORKFLOW_REQUEST_FORMAT:
            raise ValueError("CreativeDirectorAgent requires a workflow request")
        action = context.metadata.revision_action
        if action not in (None, RevisionAction.revise_brief):
            raise ValueError("CreativeDirectorAgent only supports revise_brief revisions")
        request_content = inputs.content
        prompt = load_prompt()
        if action == RevisionAction.revise_brief:
            request_content = {
                **context.original_request.model_dump(mode="json"),
                "revision": _revision_data(context),
            }
            prompt += "\n\n" + load_revision_prompt()
        text = (
            request_content
            if isinstance(request_content, str)
            else json.dumps(request_content, ensure_ascii=False, indent=2)
        )
        invoke = call_hugging_face
        if self._api_key is not None or self.endpoint != HF_CHAT_URL or self.timeout_seconds != 120:
            async def invoke(provider, model, request_data, parameters):
                return await call_hugging_face(provider, model, request_data, parameters,
                    api_key=self._api_key.get_secret_value() if self._api_key is not None else None,
                    endpoint=self.endpoint, timeout_seconds=self.timeout_seconds)
        model_text = await context.call_model(
            provider="huggingface",
            model=self.model,
            request_data={"messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ]},
            parameters={
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
            },
            invoke=invoke,
        )
        try:
            content = json.loads(model_text)
        except (TypeError, json.JSONDecodeError):
            raise ValueError("The model's creative brief must be valid JSON without Markdown fences.") from None
        validate_brief(content)
        expected_keywords = [item.text for item in context.original_request.keywords]
        if content["keywords"] != expected_keywords:
            raise ValueError("brief.keywords must preserve the original keywords in order")
        return AgentOutput(content=content, content_format=BRIEF_FORMAT)


class OfflineCreativeDirectorAgent(Agent):
    """Deterministic sample for offline checks, selected only with --offline."""

    async def run(self, inputs: AgentInput, context: AgentContext) -> AgentOutput:
        if inputs.content_format != WORKFLOW_REQUEST_FORMAT:
            raise ValueError("CreativeDirectorAgent requires a workflow request")
        request = context.original_request
        tone = next(
            (item.value for item in request.constraints if item.name == "tone"),
            "balanced",
        )
        colors = next(
            (item.value for item in request.constraints if item.name == "palette"),
            ["forest green", "cream"],
        )
        keywords = [item.text for item in request.keywords]
        theme = ", ".join(keywords)
        # Deterministic example brief; replace this dictionary with parsed model JSON.
        content = {
            "keywords": keywords,
            "interpretation": f"Connect {theme} through one quiet everyday moment.",
            "audience": "People seeking a quiet break in their day.",
            "objective": "Invite the viewer to pause and consider the central theme.",
            "core_message": "Make room for a quiet moment.",
            "concept": {
                "title": "A Quiet Moment",
                "central_idea": "One familiar object becomes the focus of an uncluttered scene.",
                "connection_to_keywords": f"Use {keywords[0]} as the main subject; express {theme} through the copy and atmosphere.",
            },
            "copy": {
                "headline": "Make Room for a Quiet Moment",
                "subtitle": " / ".join(keywords),
                "call_to_action": "",
            },
            "visual_direction": {
                "main_subject": f"A single subject representing {keywords[0]}.",
                "setting": "A plain cream background with soft natural light.",
                "visual_relationship": "Isolate the subject in open space to suggest a pause.",
                "supporting_elements": [],
                "style": "Minimal editorial illustration",
                "mood": tone,
            },
            "layout": {
                "focal_point": "The single main subject",
                "composition": "Portrait composition with generous negative space",
                "reading_order": "Headline, main subject, then subtitle",
                "typography": "Clear sans-serif headline and smaller supporting text",
            },
            "palette": {
                "colors": colors,
                "rationale": "A restrained palette supports the quiet atmosphere.",
            },
            "avoid": ["Visual clutter", "Invented statistics", "Brand affiliations"],
        }
        validate_brief(content)
        return AgentOutput(content=content, content_format=BRIEF_FORMAT)


def registration(
    *, model: str | None = None, offline: bool = False,
) -> AgentRegistration:
    return AgentRegistration(
        role=AgentRole.creative_director,
        instance_factory=lambda config: (
            OfflineCreativeDirectorAgent() if offline else CreativeDirectorAgent(
                model=config["model"],
            )
        ),
        input_formats=frozenset({WORKFLOW_REQUEST_FORMAT}),
        output_formats=frozenset({BRIEF_FORMAT}),
        output_validator=validate_brief,
        config={
            "model": model or os.environ.get("HF_MODEL", "").strip() or DEFAULT_HF_MODEL,
        },
    )


async def run_creative_director(
    root: Path, *, model: str | None = None, offline: bool = False,
    request_path: Path | None = None,
) -> dict[str, object]:
    input_path = request_path or (Path(__file__).parent / "resources" / "sample-request.json")
    sample = json.loads(input_path.read_text(encoding="utf-8"))
    request = OriginalRequest.from_keywords(
        "creative-director",
        sample["keywords"],
        sample.get("constraints", {}),
    )
    result = await run_single_agent_from_request(
        registration(model=model, offline=offline),
        request,
        root=root,
    )
    output_data = result.agent_result.output.model_dump(mode="json")
    public_fields = {"content", "content_format", "artifacts", "review"}
    if set(output_data) != public_fields:
        raise AssertionError("AgentOutput public fields do not match the interface")
    if result.metadata.identity.execution_mode != ExecutionMode.real:
        raise AssertionError("creative director must use the default real mode")
    validate_brief(output_data["content"])
    return {
        "run_id": result.metadata.identity.run_id,
        "execution_mode": result.metadata.identity.execution_mode.value,
        "output": output_data,
        "record_path": str(result.record_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a creative brief using Hugging Face.")
    parser.add_argument("--root", type=Path, default=Path(".scratch/creative-director"))
    parser.add_argument("--input", type=Path, help="Request JSON containing keywords and optional constraints")
    parser.add_argument("--model", help="Hugging Face model ID; defaults to HF_MODEL or Qwen3-4B-Instruct-2507")
    parser.add_argument("--offline", action="store_true", help="Run the deterministic sample without an API call")
    arguments = parser.parse_args()
    try:
        result = asyncio.run(run_creative_director(
            arguments.root, model=arguments.model, offline=arguments.offline,
            request_path=arguments.input,
        ))
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f"Creative director failed: {error}\n")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()

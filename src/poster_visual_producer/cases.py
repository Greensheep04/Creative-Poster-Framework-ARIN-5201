"""Manual upstream cases for A3 integration; no simulated quality pass."""

from __future__ import annotations
import argparse, asyncio, copy, json, time
from pathlib import Path
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field
from poster_framework import (
    AgentRegistration,
    AgentRole,
    AgentInput,
    AgentOutput,
    OriginalRequest,
    WORKFLOW_REQUEST_FORMAT,
    ExecutionIdentity,
    ExecutionMode,
    FrameworkMetadata,
    ContentProvenance,
    RunSession,
    run_single_agent,
    run_single_agent_from_upstream,
)
from .contracts import CreativeBrief, PosterSpec, validate_pair
from .registration import BRIEF_FORMAT, SPEC_FORMAT, registration, project_budget
from .fonts import FontRegistry


class PresetCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    keywords: list[str] = Field(min_length=2, max_length=5)
    brief: dict
    spec: dict


def materialize_case(case, request):
    brief, spec = copy.deepcopy(case.brief), copy.deepcopy(case.spec)
    bindings = brief.get("keyword_bindings", [])
    source_ids = [r["keyword_id"] for r in bindings]
    if len(source_ids) != len(request.keywords) or len(set(source_ids)) != len(
        source_ids
    ):
        raise ValueError(
            "Each preset keyword needs one unique Brief binding in keyword order"
        )
    mapping = dict(zip(source_ids, (k.keyword_id for k in request.keywords)))
    for doc in (brief, spec):
        doc["run_id"] = request.run_id
        for field in ("keyword_bindings", "keyword_element_bindings"):
            for row in doc.get(field, []):
                row["keyword_id"] = mapping[row["keyword_id"]]
        for row in doc.get("asset_plan", []):
            row["required_keyword_ids"] = [
                mapping[k] for k in row["required_keyword_ids"]
            ]
    # Preserve the authored business versions, copy and geometry exactly.
    b = CreativeBrief.model_validate(brief)
    s = PosterSpec.model_validate(spec)
    validate_pair(s, b, [k.keyword_id for k in request.keywords])
    return brief, spec


class PresetPublisher:
    def __init__(self, content, fmt):
        self.content, self.fmt = content, fmt

    async def run(self, inputs, context):
        return AgentOutput(content=copy.deepcopy(self.content), content_format=self.fmt)


def publisher_registration(role, content, input_format, output_format):
    return AgentRegistration(
        role=role,
        instance_factory=lambda config: PresetPublisher(content, output_format),
        input_formats=frozenset({input_format}),
        output_formats=frozenset({output_format}),
    )


def artifact_path(root, reference):
    # Framework references have already been validated by the executing session.
    return Path(root) / reference["uri"].removeprefix("runs/")


async def run_case(case, generator, fonts, root, *, seed=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    request = OriginalRequest.from_keywords("case-" + uuid4().hex, case.keywords)
    try:
        brief, spec = materialize_case(case, request)
        session = RunSession(request, root=root, budget=project_budget())
        identity = ExecutionIdentity(
            run_id=request.run_id,
            role=AgentRole.creative_director,
            invocation_id="preset-" + uuid4().hex,
            execution_mode=ExecutionMode.real,
        )
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
                run_id=request.run_id, source_id=request.request_id
            ),
        )
        await run_single_agent(
            publisher_registration(
                AgentRole.creative_director,
                brief,
                WORKFLOW_REQUEST_FORMAT,
                BRIEF_FORMAT,
            ),
            inputs,
            session=session,
            record_path=root / "a1-record.json",
        )
        await run_single_agent_from_upstream(
            publisher_registration(
                AgentRole.layout_planner, spec, BRIEF_FORMAT, SPEC_FORMAT
            ),
            root / "a1-record.json",
            root=root,
            record_path=root / "a2-record.json",
        )
        result = await run_single_agent_from_upstream(
            registration(generator, fonts, seed=seed),
            root / "a2-record.json",
            root=root,
            record_path=root / "a3-record.json",
        )
        candidate = result.output.content
        (root / "poster.png").write_bytes(
            artifact_path(root, candidate["candidate_ref"]).read_bytes()
        )
        (root / "render_manifest.json").write_bytes(
            artifact_path(root, candidate["manifest_ref"]).read_bytes()
        )
        (root / "candidate.json").write_text(
            json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for a in candidate["assets"]:
            (root / (a["asset_id"] + "-source.png")).write_bytes(
                artifact_path(root, a["source_ref"]).read_bytes()
            )
        summary = {
            "case": case.name,
            "keywords": case.keywords,
            "status": "rendered",
            "source_mode": candidate["source_mode"],
            "visual_quality_evaluated": False,
            "manual_review_required": True,
            "seed": seed,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "usage": RunSession.load(
                root / "a3-record.json", root=root
            ).usage.model_dump(),
            "poster": str((root / "poster.png").resolve()),
            "record": str((root / "a3-record.json").resolve()),
        }
    except Exception as error:
        summary = {
            "case": case.name,
            "keywords": case.keywords,
            "status": "failed",
            "error_type": type(error).__name__,
            "error_code": getattr(error, "code", None),
            "visual_quality_evaluated": False,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        (root / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        raise
    (root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


async def main_async(args):
    from .backends import create_generator
    from .generators import FixtureImageGenerator

    if args.fixture:
        data = args.fixture.read_bytes()
        generator = FixtureImageGenerator(lambda request: data)
    else:
        generator = create_generator(
            json.loads(args.backend_config.read_text(encoding="utf-8"))
        )
    fonts = FontRegistry.from_config(
        json.loads(args.fonts.read_text(encoding="utf-8")), args.font_base
    )
    summaries = []
    for path in args.case:
        case = PresetCase.model_validate_json(path.read_text(encoding="utf-8"))
        summary = await run_case(
            case, generator, fonts, args.root / case.name, seed=args.seed
        )
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, action="append", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--fonts", type=Path, required=True)
    parser.add_argument("--font-base", type=Path, default=Path.cwd())
    parser.add_argument("--seed", type=int, default=None)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--backend-config", type=Path)
    mode.add_argument("--fixture", type=Path)
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()

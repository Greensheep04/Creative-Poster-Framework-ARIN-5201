"""Four-role integration demo; A1/A2/A4 are deterministic stand-ins, not model implementations."""

from pathlib import Path
import argparse, asyncio, copy, json, sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from examples.visual.render_fixture import ocean_fixture
from poster_framework import (
    AgentRegistration,
    AgentRole,
    AgentOutput,
    AgentSet,
    PosterWorkflow,
    ContentFormat,
    WORKFLOW_REQUEST_FORMAT,
    ReviewRecord,
    ReviewDecision,
    RevisionAction,
    VersionDependency,
    KeywordReviewResult,
    KeywordReviewStatus,
)
from poster_visual_producer.contracts import PosterSpec, CreativeBrief
from poster_visual_producer.fonts import FontRegistry
from poster_visual_producer.generators import FixtureImageGenerator
from poster_visual_producer.registration import (
    BRIEF_FORMAT,
    SPEC_FORMAT,
    CANDIDATE_FORMAT,
    registration,
    project_budget,
)

QA_FORMAT = ContentFormat(name="poster.demo-qa", version="0.1.0")


def document(name):
    return json.loads(
        (ROOT / "examples/visual" / (name + ".json")).read_text(encoding="utf-8")
    )


def map_keywords(data, request):
    ids = {old: k.keyword_id for old, k in zip(["k1", "k2", "k3"], request.keywords)}
    for key in ("keyword_bindings", "keyword_element_bindings"):
        for row in data.get(key, []):
            row["keyword_id"] = ids[row["keyword_id"]]
    for row in data.get("asset_plan", []):
        row["required_keyword_ids"] = [ids[k] for k in row["required_keyword_ids"]]
    data["run_id"] = request.run_id
    return data


class DemoCreativeDirector:
    def __init__(self, language="zh"):
        self.language = language

    async def run(self, inputs, context):
        brief = map_keywords(document("brief"), context.original_request)
        if self.language == "en":
            brief["copy"]["title"]["text"] = "OCEAN / STILLNESS"
            brief["copy"]["subtitle"]["text"] = "A little calm, a little blue."
        return AgentOutput(content=brief, content_format=BRIEF_FORMAT)


class DemoLayoutPlanner:
    def __init__(self, scenario):
        self.scenario = scenario

    async def run(self, inputs, context):
        spec = map_keywords(document("spec"), context.original_request)
        for element in spec["elements"]:
            if element["type"] == "text":
                element["text"] = inputs.content["copy"][element["copy_ref"]]["text"]
        if inputs.content["copy"]["title"]["text"].startswith("OCEAN"):
            spec["elements"][1].update(font_size_px=44, line_height_px=60)
        if context.metadata.revision_action == RevisionAction.revise_layout:
            spec["spec_version"] = 2
            spec["elements"][1]["box"]["y"] = 84
            if self.scenario == "revise_layout_asset":
                spec["asset_plan"][0]["prompt"] += " Add subtle silver reflections."
        return AgentOutput(content=spec, content_format=SPEC_FORMAT)


class DemoQualityCritic:
    def __init__(self, scenario):
        self.scenario = scenario
        self.calls = 0

    async def run(self, inputs, context):
        self.calls += 1
        content = inputs.content
        repair = self.calls == 1 and self.scenario != "initial"
        action = (
            None
            if not repair
            else (
                RevisionAction.revise_layout
                if self.scenario.startswith("revise_layout")
                else RevisionAction(self.scenario)
            )
        )
        if repair:
            if action == RevisionAction.regenerate_asset:
                ref = content["assets"][0]["source_ref"]
            elif action == RevisionAction.rerender:
                ref = content["candidate_ref"]
            else:
                ref = next(
                    a["reference"]
                    for a in context.memory.context()["artifacts"]
                    if a["name"] == "layout"
                )
            target = VersionDependency(
                artifact_id=ref["artifact_id"], revision=ref["revision"]
            )
        else:
            target = None
        checks = [
            KeywordReviewResult(
                keyword_id=k.keyword_id, status=KeywordReviewStatus.pass_
            )
            for k in context.original_request.keywords
        ]
        details = {
            "source_mode": "offline_deterministic_qa",
            "visual_quality_evaluated": False,
        }
        if repair:
            details["repair_plan"] = {
                "expected_versions": content["based_on"],
                "constraints": ["Keep the single continuous ocean horizon"],
            }
        review = ReviewRecord(
            candidate_id=content["candidate_id"],
            candidate_revision=content["candidate_ref"]["revision"],
            decision=ReviewDecision.fail if repair else ReviewDecision.pass_,
            action=action,
            target=target,
            keyword_results=tuple(checks),
            details=details,
        )
        return AgentOutput(content=details, content_format=QA_FORMAT, review=review)


async def run_demo(root: Path, scenario="initial", language="zh", budget=None):
    # One critic instance tracks the engineering repair scenario within this run.
    fonts = FontRegistry.from_config(document("fonts"), ROOT)
    count = [0]

    def source(request):
        variant = count[0]
        count[0] += 1
        return ocean_fixture(variant)

    generator = FixtureImageGenerator(source)
    critic = DemoQualityCritic(scenario)
    registrations = [
        AgentRegistration(
            role=AgentRole.creative_director,
            instance_factory=lambda config: DemoCreativeDirector(language),
            input_formats=frozenset({WORKFLOW_REQUEST_FORMAT}),
            output_formats=frozenset({BRIEF_FORMAT}),
            output_validator=lambda content: CreativeBrief.model_validate(content),
        ),
        AgentRegistration(
            role=AgentRole.layout_planner,
            instance_factory=lambda config: DemoLayoutPlanner(scenario),
            input_formats=frozenset({BRIEF_FORMAT}),
            output_formats=frozenset({SPEC_FORMAT}),
            output_validator=lambda content: PosterSpec.model_validate(content),
        ),
        registration(generator, fonts),
        AgentRegistration(
            role=AgentRole.quality_critic,
            instance_factory=lambda config: critic,
            input_formats=frozenset({CANDIDATE_FORMAT}),
            output_formats=frozenset({QA_FORMAT}),
        ),
    ]
    workflow = PosterWorkflow(
        AgentSet(registrations),
        root=root,
        budget=budget or project_budget(),
        configuration={
            "source_mode": "offline_fixture",
            "visual_quality_evaluated": False,
        },
    )
    result = await workflow.run(["海洋", "宁静", "蓝色"])
    root = Path(root)
    poster = (
        str(root / Path(result.candidate.uri.removeprefix("runs/")))
        if result.candidate
        else None
    )
    summary = {
        "scenario": scenario,
        "language": language,
        "source_mode": "offline_fixture",
        "visual_quality_evaluated": False,
        "status": result.status.value,
        "poster": poster,
        "record_path": str(result.record_path),
        "usage": result.usage.model_dump(),
        "revision_rounds": result.revision_rounds,
        "call_sequence": [x.value for x in result.call_sequence],
        "candidate": result.candidate.model_dump(mode="json")
        if result.candidate
        else None,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "runs/visual-demo")
    parser.add_argument(
        "--scenario",
        choices=[
            "initial",
            "rerender",
            "regenerate_asset",
            "revise_layout",
            "revise_layout_asset",
        ],
        default="initial",
    )
    parser.add_argument("--language", choices=["zh", "en"], default="zh")
    args = parser.parse_args()
    _, summary = asyncio.run(run_demo(args.root, args.scenario, args.language))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Real local-GPU acceptance: authored upstream cases, no automatic aesthetic pass."""

from __future__ import annotations
import argparse, asyncio, copy, json, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageDraw, ImageFont
from poster_framework import (
    AgentSet,
    AgentRegistration,
    AgentRole,
    AgentOutput,
    PosterWorkflow,
    ContentFormat,
    ReviewRecord,
    ReviewDecision,
    RevisionAction,
    VersionDependency,
    KeywordReviewResult,
    KeywordReviewStatus,
    ArtifactPurpose,
    WORKFLOW_REQUEST_FORMAT,
)
from poster_visual_producer.cases import (
    PresetCase,
    materialize_case,
    publisher_registration,
    artifact_path,
    run_case,
)
from poster_visual_producer.registration import (
    BRIEF_FORMAT,
    SPEC_FORMAT,
    CANDIDATE_FORMAT,
    registration,
    project_budget,
)
from poster_visual_producer.backends import create_generator
from poster_visual_producer.fonts import FontRegistry

QA_FORMAT = ContentFormat(name="poster.engineering-probe", version="0.1.0")


class CasePublisher:
    def __init__(self, case, role):
        self.case, self.role = case, role

    async def run(self, inputs, context):
        brief, spec = materialize_case(self.case, context.original_request)
        if self.role == AgentRole.creative_director:
            return AgentOutput(content=brief, content_format=BRIEF_FORMAT)
        if context.metadata.revision_action == RevisionAction.revise_layout:
            spec["spec_version"] += 1
            next(e for e in spec["elements"] if e["id"] == "title")["box"]["y"] += 20
        return AgentOutput(content=spec, content_format=SPEC_FORMAT)


class EngineeringRepairCritic:
    def __init__(self, action):
        self.action, self.calls = RevisionAction(action), 0

    async def run(self, inputs, context):
        self.calls += 1
        candidate = inputs.content
        details = {
            "source_mode": "manual_engineering_probe",
            "visual_quality_evaluated": False,
            "synthetic_review": True,
        }
        target = None
        action = None
        if self.calls == 1:
            action = self.action
            ref = (
                candidate["assets"][0]["source_ref"]
                if action == RevisionAction.regenerate_asset
                else next(
                    a["reference"]
                    for a in context.memory.context()["artifacts"]
                    if a["name"] == "layout"
                )
            )
            target = VersionDependency(
                artifact_id=ref["artifact_id"], revision=ref["revision"]
            )
            details["repair_plan"] = {
                "expected_versions": candidate["based_on"],
                "constraints": [
                    "Keep the same calm blue ocean subject; use a single straight natural horizon and finer water ripples."
                ],
            }
        review = ReviewRecord(
            candidate_id=candidate["candidate_id"],
            candidate_revision=candidate["candidate_ref"]["revision"],
            decision=ReviewDecision.fail if action else ReviewDecision.unknown,
            action=action,
            target=target,
            keyword_results=tuple(
                KeywordReviewResult(
                    keyword_id=k.keyword_id,
                    status=KeywordReviewStatus.pass_
                    if action
                    else KeywordReviewStatus.unknown,
                )
                for k in context.original_request.keywords
            ),
            details=details,
        )
        return AgentOutput(content=details, content_format=QA_FORMAT, review=review)


async def run_repair_probe(case, generator, fonts, root, action, *, seed=42):
    root = Path(root)
    critic = EngineeringRepairCritic(action)
    regs = [
        AgentRegistration(
            role=AgentRole.creative_director,
            instance_factory=lambda config: CasePublisher(
                case, AgentRole.creative_director
            ),
            input_formats=frozenset({WORKFLOW_REQUEST_FORMAT}),
            output_formats=frozenset({BRIEF_FORMAT}),
        ),
        AgentRegistration(
            role=AgentRole.layout_planner,
            instance_factory=lambda config: CasePublisher(
                case, AgentRole.layout_planner
            ),
            input_formats=frozenset({BRIEF_FORMAT}),
            output_formats=frozenset({SPEC_FORMAT}),
        ),
        registration(generator, fonts, seed=seed),
        AgentRegistration(
            role=AgentRole.quality_critic,
            instance_factory=lambda config: critic,
            input_formats=frozenset({CANDIDATE_FORMAT}),
            output_formats=frozenset({QA_FORMAT}),
        ),
    ]
    result = await PosterWorkflow(
        AgentSet(regs),
        root=root,
        budget=project_budget(),
        configuration={"visual_quality_evaluated": False},
    ).run(case.keywords)
    candidates = [r for r in result.artifacts if r.purpose == ArtifactPurpose.candidate]
    assert len(candidates) == 2, "Probe needs initial and repaired candidates"
    before = artifact_path(root, candidates[0].model_dump(mode="json"))
    after = artifact_path(root, candidates[1].model_dump(mode="json"))
    root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(before, root / "before.png")
    shutil.copyfile(after, root / "poster.png")
    a, b = Image.open(before), Image.open(after)
    checks = {"canvas": b.size == (720, 1280), "two_candidates": len(candidates) == 2}
    if action == "revise_layout":
        checks.update(
            no_extra_generation=result.usage.image_operations == 1,
            hero_pixels_preserved=a.crop((36, 260, 684, 1110)).tobytes()
            == b.crop((36, 260, 684, 1110)).tobytes(),
            title_pixels_changed=a.crop((36, 64, 684, 236)).tobytes()
            != b.crop((36, 64, 684, 236)).tobytes(),
        )
    else:
        checks.update(
            one_extra_generation=result.usage.image_operations == 2,
            title_pixels_preserved=a.crop((36, 64, 684, 216)).tobytes()
            == b.crop((36, 64, 684, 216)).tobytes(),
            hero_changed=a.crop((36, 260, 684, 1110)).tobytes()
            != b.crop((36, 260, 684, 1110)).tobytes(),
        )
    row = {
        "action": action,
        "workflow_status": result.status.value,
        "visual_quality_evaluated": False,
        "usage": result.usage.model_dump(),
        "call_sequence": [r.value for r in result.call_sequence],
        "checks": checks,
        "poster": str((root / "poster.png").resolve()),
        "record": str(result.record_path),
    }
    (root / "probe-report.json").write_text(
        json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert all(checks.values()), checks
    return row


async def main_async(args):
    generator = create_generator(
        json.loads(args.backend_config.read_text(encoding="utf-8"))
    )
    fonts = FontRegistry.from_config(
        json.loads((ROOT / "examples/visual/fonts.json").read_text(encoding="utf-8")),
        ROOT,
    )
    cases = []
    rows = []
    for name in ("ocean", "forest", "city"):
        case = PresetCase.model_validate_json(
            (ROOT / f"examples/visual/cases/{name}.json").read_text(encoding="utf-8")
        )
        cases.append(case)
        row = await run_case(case, generator, fonts, args.root / name, seed=args.seed)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    probes = []
    if args.exercise_repairs:
        for action in ("revise_layout", "regenerate_asset"):
            row = await run_repair_probe(
                cases[0], generator, fonts, args.root / action, action, seed=args.seed
            )
            probes.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    font = ImageFont.truetype(
        str(ROOT / "resources/fonts/NotoSansCJKsc-Regular.otf"), 22
    )
    sheet = Image.new("RGB", (1080, 735), "#EDF1F4")
    draw = ImageDraw.Draw(sheet)
    for i, (row, case) in enumerate(zip(rows, cases)):
        draw.text(
            (i * 360 + 20, 16), " / ".join(case.keywords), font=font, fill="#243B50"
        )
        im = Image.open(row["poster"]).resize((330, 587), Image.Resampling.LANCZOS)
        sheet.paste(im, (i * 360 + 15, 60))
    draw.text(
        (20, 673),
        "SDXL 本地 GPU · 固定人工 Brief/Spec · 待人工视觉审核",
        font=font,
        fill="#243B50",
    )
    sheet.save(args.root / "contact-sheet.png")
    report = {
        "backend": "local_sdxl",
        "seed": args.seed,
        "visual_quality_evaluated": False,
        "cases": rows,
        "repair_probes": probes,
    }
    (args.root / "acceptance-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("Preview: " + str(args.root / "contact-sheet.png"), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend-config",
        type=Path,
        default=ROOT / "examples/visual/backend-sdxl.json",
    )
    parser.add_argument("--root", type=Path, default=ROOT / "runs/local-gpu-preview")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--exercise-repairs", action="store_true")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()

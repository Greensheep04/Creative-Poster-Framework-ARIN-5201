"""Offline A2 v2 -> A3 renderer -> actual A4 engine; observations are explicitly unknown."""
from pathlib import Path
import argparse
import asyncio
import json
from poster_framework import AgentRegistration, AgentRole, AgentSet, PosterWorkflow, WORKFLOW_REQUEST_FORMAT, ExecutionMode
from poster_visual_producer.registration import BRIEF_FORMAT, registration, project_budget
from poster_visual_producer.fonts import FontRegistry
from poster_visual_producer.generators import FixtureImageGenerator
from poster_visual_producer.team_integration import layout_registration, critic_audit
from poster_visual_producer.qc_bridge import qc_registration
from poster_critic.ports import ModelResponse
from examples.visual.workflow_demo import DemoCreativeDirector, document, ROOT
from examples.visual.render_fixture import ocean_fixture


class UnknownObservationProvider:
    name = "offline-unknown-fixture"
    live = False

    async def invoke(self, audit, prompt):
        observation = {
            "keyword_checks": [{"keyword_id": k.keyword_id, "status": "unknown", "evidence": "Offline fixture does not inspect images."} for k in audit.keywords],
            "constraint_checks": [{"constraint_id": c.constraint_id, "status": "unknown", "evidence": "Offline fixture."} for c in audit.user_constraints],
            "text_checks": [{"copy_id": c.copy_id, "observed_text": None, "status": "unknown", "readability": "unknown"} for c in audit.approved_copy or ()],
            "visual_checks": [{"check_id": c, "status": "unknown", "evidence": "Requires actual visual review."} for c in ("text_readability", "layout_quality")],
            "issues": [], "uncertainties": ["No model vision or OCR was run."]}
        return ModelResponse(text=json.dumps(observation))


async def run(root, template, language):
    fonts = FontRegistry.from_config(document("fonts"), ROOT)
    agents = AgentSet([
        AgentRegistration(role=AgentRole.creative_director, instance_factory=lambda c: DemoCreativeDirector(language), input_formats=frozenset({WORKFLOW_REQUEST_FORMAT}), output_formats=frozenset({BRIEF_FORMAT})),
        layout_registration(fonts, font_family_key="noto_sans_sc", template_id=template),
        registration(FixtureImageGenerator(lambda r: ocean_fixture(0)), fonts),
        qc_registration(critic_audit(UnknownObservationProvider(), max_retries=0), bundle_root=root/"reviews")])
    result = await PosterWorkflow(agents, root=root/"store", budget=project_budget()).run(
        ["海洋", "宁静", "蓝色"], execution_mode=ExecutionMode.fake)
    summary = {"status": result.status.value, "image_operations": result.usage.image_operations,
               "critic_calls": result.usage.critic_vision_calls, "visual_quality_evaluated": False,
               "a1": "fixture publisher", "a2": "team v2 planner", "a3": "real Pillow renderer with fixture asset",
               "a4": "team AuditEngine with unknown observation fixture", "record_path": str(result.record_path)}
    (root/"summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--template", choices=("framed_hero_v1", "full_bleed_v1", "split_stack_v1"), default="framed_hero_v1")
    parser.add_argument("--language", choices=("zh", "en"), default="zh")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.root, args.template, args.language)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

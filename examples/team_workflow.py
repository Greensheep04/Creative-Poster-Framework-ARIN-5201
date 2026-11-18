"""One explicit entry point for the integrated four-role workflow."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4
from poster_framework import AgentSet, ExecutionMode, PosterWorkflow, RepairAuthorization, RevisionAction, RunBudget, VersionDependency
from poster_visual_producer.team_creative import creative_registration
from poster_visual_producer.team_integration import layout_registration, critic_audit
from poster_visual_producer.registration import registration
from poster_visual_producer.fonts import FontRegistry
from poster_visual_producer.qc_bridge import qc_registration
from poster_visual_producer.generators import FixtureImageGenerator
from examples.visual.team_integration import UnknownObservationProvider
from examples.visual.render_fixture import ocean_fixture

ROOT = Path(__file__).resolve().parents[1]
REPAIRS = ("revise_brief", "revise_layout", "rerender", "regenerate_asset")


def live_prerequisites(*, backend_config, critic_config, critic_model=None):
    missing = []
    if not os.environ.get("HF_TOKEN", "").strip():
        missing.append("A1: set HF_TOKEN (Hugging Face Inference Providers permission required)")
    if backend_config is None:
        missing.append("A3: provide --backend-config and POSTER_SDXL_MODEL_DIR for local SDXL")
    elif not Path(backend_config).is_file():
        missing.append("A3: backend configuration file is missing")
    if critic_config is None:
        missing.append("A4: provide --critic-config with the verified model, HTTPS base URL and api_key_env")
    else:
        from poster_critic.providers.chat import load_config, valid_key_for_config
        try:
            config = load_config(critic_config, critic_model)
            if "YOUR" in config.base_url or "YOUR" in config.model:
                missing.append("A4: replace template endpoint/model placeholders")
            if not valid_key_for_config(config, os.environ.get(config.api_key_env, "")):
                missing.append("A4: set " + config.api_key_env + " for the selected vision service")
        except (ValueError, OSError) as error:
            missing.append("A4: invalid config or model selection")
    return missing


async def run_team(root, keywords, *, offline, offline_repair=None, backend_config=None,
                   critic_config=None, critic_model=None, a1_model=None, constraints=None,
                   fonts_config=None, font_base=None, max_revision_rounds=1, project=None):
    if offline_repair is not None and (not offline or offline_repair not in REPAIRS):
        raise ValueError("offline repair requires explicit offline mode and a supported action")
    if project is not None:
        max_revision_rounds = project.budget.max_revision_rounds
        if constraints is None: constraints = project.runtime.constraints
    if not 0 <= max_revision_rounds <= 1:
        raise ValueError("This integration trial permits zero or one revision round")
    if not offline:
        missing = project.live_missing() if project is not None else live_prerequisites(backend_config=backend_config, critic_config=critic_config, critic_model=critic_model)
        if missing:
            raise ValueError("Missing live prerequisites: " + "; ".join(missing))
    root = Path(root).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("Use a new or empty output directory")
    if project is not None:
        fonts = FontRegistry.from_config(project.font_mapping(), Path("."))
    else:
        fonts = FontRegistry.from_config(json.loads(Path(fonts_config or ROOT/"examples/visual/fonts.json").read_text(encoding="utf-8-sig")), Path(font_base or ROOT))
    if offline:
        generator = FixtureImageGenerator(lambda request: ocean_fixture(0))
        provider = UnknownObservationProvider()
    else:
        from poster_visual_producer.backends import create_generator
        from poster_critic.providers.chat import ChatProvider, load_config
        if project is not None:
            from poster_critic.providers.chat import ProviderConfig
            generator = create_generator(project.agents.a3.backend, api_key=project.agents.a3.api_key.get_secret_value())
            provider = ChatProvider(ProviderConfig.model_validate(project.agents.a4.provider), api_key=project.agents.a4.api_key.get_secret_value())
        else:
            generator = create_generator(json.loads(Path(backend_config).read_text(encoding="utf-8-sig")))
            provider = ChatProvider(load_config(critic_config, critic_model))
    async def authorize(feedback, context):
        # An explicitly selected controlled test; never infer authorization from unknown.
        candidate = feedback["current_candidate"]
        if offline_repair == "revise_brief": target = feedback["brief_dependency"]
        elif offline_repair == "revise_layout": target = feedback["layout_dependency"]
        elif offline_repair == "regenerate_asset": target = candidate["assets"][0]["source_ref"]
        else: target = candidate["candidate_ref"]
        return RepairAuthorization(authorization_id="offline-test-" + uuid4().hex,
            based_on_candidate=VersionDependency(artifact_id=candidate["candidate_id"],revision=candidate["candidate_ref"]["revision"]),
            action=RevisionAction(offline_repair), target=VersionDependency(artifact_id=target["artifact_id"],revision=target["revision"]),
            expected_versions=candidate["based_on"], preserve=[])
    def offline_layout_patch(context):
        layout = next(i["content"] for i in context.memory.context()["artifacts"] if i["name"] == "layout")
        title = next(e for e in layout["elements"] if e["id"] == "title")
        return {"preserve_copy":True,"changes":[{"target_type":"element","target_id":"title",
            "field":"align","old_value":title["align"],"new_value":"center" if title["align"] != "center" else "left"}]}
    a1_options = None
    if project is not None:
        a1 = project.agents.a1
        a1_model = a1.model
        a1_options = {"api_key":a1.api_key.get_secret_value(), "endpoint":a1.endpoint,
            "timeout_seconds":a1.timeout_seconds,"max_tokens":a1.max_tokens,"temperature":a1.temperature}
    layout_options = {"font_family_key":"noto_sans_sc","adaptive":True}
    critic_options = {"max_retries":0}
    visual_options = {}
    if project is not None:
        layout_options = project.agents.a2.model_dump()
        critic_options = {key:getattr(project.agents.a4,key) for key in ("max_retries","request_timeout","total_timeout")}
        visual_options = {"seed":project.runtime.seed if generator.capabilities.supports_seed else None}
    agents = AgentSet([creative_registration(offline=offline, model=a1_model, options=a1_options),
        layout_registration(fonts, **layout_options, revision_patch=offline_layout_patch if offline_repair == "revise_layout" else None),
        registration(generator, fonts, **visual_options),
        qc_registration(critic_audit(provider, **critic_options), bundle_root=root/"reviews")])
    budget = project.budget if project is not None else RunBudget(max_revision_rounds=max_revision_rounds,
        max_image_operations=2,max_critic_vision_calls=2,max_model_calls=6)
    effective = project.redacted() if project is not None else None
    if effective is not None:
        effective["runtime"].update(mode="offline" if offline else "live", output_root=str(root),keywords=list(keywords), constraints=constraints, seed=visual_options.get("seed"))
    workflow = PosterWorkflow(agents,root=root/"store",budget=budget,
        repair_authorizer=authorize if offline_repair else None,
        configuration={"integration_mode": "offline" if offline else "live", "offline_repair":offline_repair, "project_config":effective})
    result = await workflow.run(keywords, constraints=constraints,
        execution_mode=ExecutionMode.fake if offline else ExecutionMode.real)
    # A budget may stop execution before any candidate exists.
    poster = None
    if result.visual is not None:
        bundles = list((root/"reviews"/result.run_id).glob("*/evidence/candidate.json"))
        current = next((p for p in bundles if json.loads(p.read_text(encoding="utf-8"))["candidate_ref"] == result.visual.content["candidate_ref"]), None)
        if current is not None:
            (root/"poster.png").write_bytes((current.parent.parent/"poster.png").read_bytes())
            poster = str(root/"poster.png")
    brief_path = None
    if result.brief is not None:
        (root/"brief.json").write_text(json.dumps(result.brief.model_dump(mode="json"),ensure_ascii=False,indent=2),encoding="utf-8")
        brief_path = str(root/"brief.json")
    if effective is not None:
        (root/"effective-config.json").write_text(json.dumps(effective,ensure_ascii=False,indent=2),encoding="utf-8")
    (root/"result.json").write_text(result.model_dump_json(indent=2),encoding="utf-8")
    summary = {"run_id":result.run_id,"mode":"offline" if offline else "live", "status":result.status.value,
        "production_pass":bool(result.review and result.review.details.get("production_pass")),
        "visual_quality_evaluated":not offline, "revision_rounds":result.revision_rounds,
        "image_operations":result.usage.image_operations,"critic_calls":result.usage.critic_vision_calls,
        "model_calls":result.usage.model_calls,"reason":result.reason,
        "call_sequence":[r.value for r in result.call_sequence],"poster":poster,
        "brief":brief_path,"record_path":str(result.record_path),
        "a1":"actual creative module (deterministic offline mode)" if offline else "Hugging Face",
        "a2":"actual team layout planner","a3":"actual renderer; fixture asset" if offline else "configured generator and renderer",
        "a4":"actual AuditEngine; unknown observation fixture" if offline else "configured vision provider"}
    (root/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    return summary


async def run_from_project_config(path, *, root=None, offline=None, keywords=None, offline_repair=None):
    from poster_framework.project_config import load_project_config
    cfg = load_project_config(path)
    mode = cfg.runtime.mode == "offline" if offline is None else offline
    output = Path(root) if root is not None else cfg.runtime.output_root/("run-"+uuid4().hex[:12])
    try:
        return await run_team(output, cfg.runtime.keywords if keywords is None else keywords,
            offline=mode,offline_repair=offline_repair,project=cfg)
    except Exception as error:
        from poster_framework.project_config import ConfigurationError
        raise ConfigurationError("Configured workflow failed: " + cfg.redact_text(str(error))) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,help="Unified local TOML (may contain private keys)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline",action="store_true"); mode.add_argument("--live",action="store_true")
    parser.add_argument("--check",action="store_true",help="Check configuration without network/model calls")
    parser.add_argument("--root",type=Path)
    parser.add_argument("--keywords",nargs="+")
    parser.add_argument("--offline-repair",choices=REPAIRS)
    parser.add_argument("--backend-config",type=Path); parser.add_argument("--critic-config",type=Path)
    parser.add_argument("--critic-model"); parser.add_argument("--a1-model")
    parser.add_argument("--fonts",type=Path); parser.add_argument("--font-base",type=Path)
    args = parser.parse_args()
    cfg = None
    try:
        if args.config is not None:
            from poster_framework.project_config import load_project_config
            if any((args.backend_config,args.critic_config,args.critic_model,args.a1_model,args.fonts,args.font_base)):
                parser.error("--config uses its agent settings; do not combine legacy per-agent configuration flags")
            cfg = load_project_config(args.config)
            offline = True if args.offline else False if args.live else cfg.runtime.mode == "offline"
            if args.check:
                missing = [] if offline else cfg.live_missing()
                print(json.dumps({"ready":not missing,"mode":"offline" if offline else "live","network_requests":0,"missing":missing},ensure_ascii=False,indent=2))
                return 2 if missing else 0
            output = args.root if args.root is not None else cfg.runtime.output_root/("run-"+uuid4().hex[:12])
            result = asyncio.run(run_team(output,cfg.runtime.keywords if args.keywords is None else args.keywords,
                offline=offline,offline_repair=args.offline_repair,project=cfg))
        else:
            if not (args.offline or args.live):parser.error("choose --offline/--live or provide --config")
            if args.check:
                if args.offline:parser.error("legacy --check requires --live")
                missing=live_prerequisites(backend_config=args.backend_config,critic_config=args.critic_config,critic_model=args.critic_model)
                print(json.dumps({"ready":not missing,"network_requests":0,"missing":missing},ensure_ascii=False,indent=2))
                return 2 if missing else 0
            if args.root is None:parser.error("--root is required without a unified config")
            result = asyncio.run(run_team(args.root,args.keywords or ["ocean","plastic","hope"],offline=args.offline,
                offline_repair=args.offline_repair,backend_config=args.backend_config,critic_config=args.critic_config,
                critic_model=args.critic_model,a1_model=args.a1_model,fonts_config=args.fonts,font_base=args.font_base))
    except (ValueError,OSError) as error:
        message = cfg.redact_text(str(error)) if cfg is not None else str(error)
        parser.exit(2,"Team workflow failed: " + message + "\n")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

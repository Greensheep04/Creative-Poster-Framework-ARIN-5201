"""Explicit adapters for the team's A2 v2 and A4 engine; upstream code stays intact."""
from __future__ import annotations

import copy
from .contracts import CreativeBrief, PosterSpec, validate_pair
from .registration import BRIEF_FORMAT, SPEC_FORMAT


def layout_registration(fonts, *, font_family_key, template_id=None, adaptive=False, revision_patch=None):
    """Use A2's planner with a string-copy view and the exact A3 font files."""
    from poster_framework import AgentRegistration, AgentRole
    from poster_framework.layout_fonts import FontFace, FontRegistry
    from poster_framework.layout_planner import LayoutPlannerAgent

    registry = FontRegistry({
        (family, weight): FontFace(family, weight, face.path, face.index, "a3-config")
        for family, weights in fonts.families.items()
        for weight, face in weights.items()
    })

    class LayoutAdapter:
        async def run(self, inputs, context):
            brief = CreativeBrief.model_validate(inputs.content)
            view = copy.deepcopy(inputs.content)
            view["copy"] = {key: block.text for key, block in brief.copy_blocks.items()}
            if adaptive:
                view["palette"] = copy.deepcopy(brief.intent.get("palette", {}))
                view["visual_direction"] = copy.deepcopy(brief.visual_requirements)
                view["avoid"] = list(brief.visual_requirements.get("avoid", []))
            if context.metadata.revision_action is not None and context.metadata.revision_action.value == "revise_layout" and revision_patch is not None:
                view["layout_patch"] = revision_patch(context)
            from poster_framework.layout_errors import TextOverflowError
            from poster_framework.layout_templates import TextSlot, SlotBox, fit_text_slot
            # Team A1's full English headlines can exceed the default one-line slot.
            # Try measured team templates without editing or truncating approved copy.
            requested = template_id or next((c.value for c in context.original_request.constraints if c.name == "template_id"), None)
            action = context.metadata.revision_action
            choices = [requested]
            if adaptive and requested is None and (action is None or action.value == "revise_brief"):
                choices = ["split_stack_v1", "full_bleed_v1", "framed_hero_v1"]
            for index, chosen in enumerate(choices):
                try:
                    output = await LayoutPlannerAgent(
                        registry=registry, font_family_key=font_family_key,
                        template_id=chosen,
                    ).run(inputs.model_copy(update={"content": view}), context)
                    break
                except TextOverflowError:
                    if index == len(choices) - 1:
                        raise
            if adaptive and action is not None and action.value == "revise_brief":
                # Framework invalidates old layout before invoking A1. Its validated
                # external authorization keeps the previous business version.
                history = context.memory.context()["revision_history"]
                target = context.metadata.revision_target.model_dump(mode="json")
                entry = next((h["changes"] for h in reversed(history)
                    if h["changes"].get("action") == "revise_brief" and h["changes"].get("target") == target), {})
                versions = (entry.get("details") or {}).get("repair_plan", {}).get("expected_versions", {})
                updated = copy.deepcopy(output.content)
                updated["spec_version"] = versions.get("spec_version", brief.brief_version - 1) + 1
                output = output.model_copy(update={"content":updated})
            if adaptive and "cta" in brief.copy_blocks and not any(e.get("copy_ref") == "cta" for e in output.content["elements"]):
                spec = copy.deepcopy(output.content)
                texts = [e for e in spec["elements"] if e["type"] == "text"]
                bottom = max(e["box"]["y"] + e["box"]["height"] for e in texts) + 20
                if spec["template_id"] == "framed_hero_v1":
                    # Keep the new exact-copy slot below the framed image.
                    bottom = max(bottom, max(e["box"]["y"] + e["box"]["height"] for e in spec["elements"] if e["type"] == "image") + 12)
                if 1244 - bottom < 26:
                    raise TextOverflowError("Mandatory CTA has no safe space; revise copy/layout")
                slot = TextSlot(element_id="cta", copy_ref="cta",
                    box=SlotBox(x=48, y=bottom, width=624, height=1244-bottom),
                    max_font_size_px=26, min_font_size_px=20, max_lines=3,
                    align=texts[-1]["align"], z=4, color_role="primary", font_weight=400)
                spec["elements"].append(fit_text_slot(slot, brief.copy_blocks["cta"].text,
                    color=texts[-1]["color"], font_family_key=font_family_key, registry=registry))
                if spec["template_id"] == "full_bleed_v1":
                    spec["allowed_overlaps"].extend([e["id"], "cta"] for e in spec["elements"] if e["type"] == "image")
                output = output.model_copy(update={"content":spec})
            # Reject unsupported/modified mandatory copy before A3 submits images.
            validate_pair(PosterSpec.model_validate(output.content), brief,
                          [k.keyword_id for k in context.original_request.keywords])
            return output

    return AgentRegistration(
        role=AgentRole.layout_planner,
        instance_factory=lambda config: LayoutAdapter(),
        input_formats=frozenset({BRIEF_FORMAT}),
        output_formats=frozenset({SPEC_FORMAT}),
    )


def critic_audit(provider, *, max_retries=1, request_timeout=30, total_timeout=90):
    """Connect the real A4 algorithm to QCBridge's portable review bundle.

    Provider construction/credentials belong to the application. Fake providers
    require a fake workflow; live providers require real. No mode substitution,
    model download, network request or release happens during configuration.
    """
    from poster_critic.adapters.local_input import load_input
    from poster_critic.adapters.framework_executor import FrameworkExecutor
    from poster_critic.engine import AuditEngine
    from poster_critic.errors import BudgetExceededError as CriticBudgetExceeded
    from poster_framework.errors import BudgetExceededError

    async def audit(manifest_path, context):
        executor = FrameworkExecutor(
            context, provider=provider.name, max_attempts=max_retries + 1,
            request_timeout=request_timeout, total_timeout=total_timeout,
        )
        try:
            report = await AuditEngine(provider, executor, max_retries=max_retries).audit(
                load_input(manifest_path))
        except CriticBudgetExceeded as error:
            raise BudgetExceededError(str(error)) from error
        return report.model_dump(mode="json")

    return audit

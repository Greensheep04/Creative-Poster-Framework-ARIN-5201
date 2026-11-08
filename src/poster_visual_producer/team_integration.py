"""Explicit adapters for the team's A2 v2 and A4 engine; upstream code stays intact."""
from __future__ import annotations

import copy
from .contracts import CreativeBrief, PosterSpec, validate_pair
from .registration import BRIEF_FORMAT, SPEC_FORMAT


def layout_registration(fonts, *, font_family_key, template_id=None):
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
            output = await LayoutPlannerAgent(
                registry=registry, font_family_key=font_family_key,
                template_id=template_id,
            ).run(inputs.model_copy(update={"content": view}), context)
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

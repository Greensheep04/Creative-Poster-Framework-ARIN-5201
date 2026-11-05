"""A2 Layout Planner: CreativeBrief -> executable 720x1280 PosterSpec.

The agent is the only owner of layout.  It may pin a template, fit copy to real
measured ink, translate a palette direction into concrete colors, map every
original keyword onto a visible element, and apply an allowlisted layout patch
during a ``revise_layout`` round.  It never rewrites the approved copy: when the
text cannot fit, the round fails with ``TEXT_OVERFLOW`` instead of truncating.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .layout_errors import LayoutContractError, TextOverflowError
from .layout_checks import check_layout_geometry, check_planned_keyword_coverage
from .layout_fonts import (
    FontRegistry,
    default_family_key,
    default_font_registry,
    measure_text_block,
)
from .layout_spec import validate_poster_spec
from .layout_templates import (
    LayoutTemplate,
    background_color,
    build_hero_element,
    describe_templates,
    fit_text_slot,
    get_template,
    hero_asset_plan,
    resolve_palette,
    slot_text_color,
    template_ids,
)
from .registration import AgentRegistration
from .types import (
    Agent,
    AgentContext,
    AgentInput,
    AgentOutput,
    AgentRole,
    ContentFormat,
    OriginalRequest,
    RevisionAction,
)

BRIEF_FORMAT = ContentFormat(name="poster.creative-brief", version="0.1.0")
LEGACY_BRIEF_FORMAT = ContentFormat(name="poster.creative-brief", version="1")
POSTER_SPEC_FORMAT = ContentFormat(name="poster.spec", version="0.1.0")

#: Backwards-compatible alias for the name used by the first A2 baseline.
LAYOUT_BRIEF_FORMAT = BRIEF_FORMAT

_PATCHABLE_FIELDS = {
    "box.x",
    "box.y",
    "box.width",
    "box.height",
    "font_size_px",
    "line_height_px",
    "max_lines",
    "align",
    "color",
    "z",
}
_NEGATIVE_PROMPT_DEFAULT = "text, logo, watermark, extra letters"


def _as_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _brief_copy(brief: Mapping[str, Any]) -> tuple[str, str | None]:
    copy = brief.get("copy")
    if isinstance(copy, Mapping):
        title = _as_text(copy.get("title")) or _as_text(copy.get("headline"))
        subtitle = _as_text(copy.get("subtitle"))
    else:
        title = _as_text(brief.get("headline")) or _as_text(brief.get("title"))
        subtitle = _as_text(brief.get("subtitle"))
    if title is None:
        raise LayoutContractError("brief must contain title or headline copy")
    return title, subtitle


def _brief_version(brief: Mapping[str, Any]) -> int:
    raw = brief.get("brief_version", 1)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise LayoutContractError("brief_version must be a positive integer")
    return raw


def _check_keywords(brief: Mapping[str, Any], request: OriginalRequest) -> None:
    raw = brief.get("keywords", brief.get("keyword_texts"))
    if raw is None:
        return
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise LayoutContractError("brief keywords must be a list of strings")
    expected = [keyword.text for keyword in request.keywords]
    if sorted(raw) != sorted(expected):
        raise LayoutContractError(
            "brief keywords must preserve the original request text; "
            f"expected {expected!r}, got {raw!r}"
        )


def _select_template(
    brief: Mapping[str, Any],
    request: OriginalRequest,
    configured: str | None,
) -> tuple[LayoutTemplate, bool]:
    candidate = configured or _as_text(brief.get("template_id"))
    if candidate is None:
        constraint = next(
            (item for item in request.constraints if item.name == "template_id"), None
        )
        if constraint is not None and isinstance(constraint.value, str):
            candidate = constraint.value
    try:
        return get_template(candidate or template_ids()[0]), candidate is not None
    except ValueError as error:
        raise LayoutContractError(str(error)) from error


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _visual_prompt(brief: Mapping[str, Any], request: OriginalRequest) -> tuple[str, str]:
    requirements = brief.get("visual_requirements")
    direction = brief.get("visual_direction")
    subject = None
    style = None
    avoid: list[str] = []
    if isinstance(requirements, Mapping):
        subject = _as_text(requirements.get("main_subject"))
        raw_avoid = requirements.get("avoid")
        if isinstance(raw_avoid, list):
            avoid.extend(str(item) for item in raw_avoid if _as_text(item))
    if isinstance(direction, Mapping):
        subject = subject or _as_text(direction.get("main_subject"))
        style = _as_text(direction.get("style"))
        raw_avoid = direction.get("avoid")
        if isinstance(raw_avoid, list):
            avoid.extend(str(item) for item in raw_avoid if _as_text(item))
    if subject is None:
        intent = brief.get("intent")
        if isinstance(intent, Mapping):
            subject = _as_text(intent.get("core_message"))
            style = style or _as_text(intent.get("visual_style"))
    if subject is None:
        subject = "、".join(keyword.text for keyword in request.keywords)
    if _has_cjk(subject):
        parts = [subject.rstrip("。.") + "。"]
        if style:
            parts.append(f"风格：{style}。")
        parts.append("为标题留出干净留白，不要生成任何文字、logo 或水印。")
        prompt = "".join(parts)
    else:
        parts = [subject.rstrip(".") + "."]
        if style:
            parts.append(f"Style: {style.rstrip('.')}.")
        parts.append("Leave clean space for typography; no text, logos or watermark.")
        prompt = " ".join(parts)
    negative = "，".join(dict.fromkeys(avoid)) if avoid else _NEGATIVE_PROMPT_DEFAULT
    return prompt, negative


def _forbidden_copy_change(preserve_copy: object) -> bool:
    return preserve_copy is not True


def _apply_layout_patch(
    previous: Mapping[str, Any],
    patch: Mapping[str, Any],
    *,
    registry: FontRegistry,
) -> dict[str, Any]:
    """Apply an allowlisted, stale-checked element patch to a previous spec."""

    spec = dict(previous)
    elements = [dict(element) for element in previous.get("elements", [])]
    by_id = {element.get("id"): element for element in elements}
    changes = patch.get("changes")
    if not isinstance(changes, list) or not changes:
        raise LayoutContractError("layout_patch requires a non-empty changes list")
    if _forbidden_copy_change(patch.get("preserve_copy", True)):
        raise LayoutContractError("layout_patch must preserve published copy")
    preserve = patch.get("preserve_element_ids", [])
    if not isinstance(preserve, list) or any(item not in by_id for item in preserve):
        raise LayoutContractError("preserve_element_ids must reference existing elements")
    touched: list[str] = []
    for change in changes:
        if not isinstance(change, Mapping):
            raise LayoutContractError("each layout change must be an object")
        if change.get("target_type") != "element":
            raise LayoutContractError("only element changes are supported")
        target_id = change.get("target_id")
        if target_id not in by_id:
            raise LayoutContractError(f"unknown patch target element: {target_id!r}")
        if target_id in preserve:
            raise LayoutContractError(
                f"element {target_id!r} is listed in preserve_element_ids and cannot be patched"
            )
        field = change.get("field")
        if field not in _PATCHABLE_FIELDS:
            raise LayoutContractError(
                f"field {field!r} is not allowed in a layout patch; "
                f"allowed: {', '.join(sorted(_PATCHABLE_FIELDS))}"
            )
        if "old_value" in change and change.get("old_value") is not None:
            current = _read_field(by_id[target_id], field)
            if current != change["old_value"]:
                raise LayoutContractError(
                    f"stale layout patch for {target_id}.{field}: "
                    f"expected {change['old_value']!r}, found {current!r}"
                )
        new_value = change.get("new_value")
        if field.startswith("box."):
            box = dict(by_id[target_id].get("box", {}))
            box[field.split(".", 1)[1]] = new_value
            by_id[target_id]["box"] = box
        else:
            by_id[target_id][field] = new_value
        touched.append(str(target_id))
    spec["elements"] = elements
    touched_ids = set(touched) - set(preserve)
    for element in elements:
        if element.get("id") not in touched_ids or element.get("type") != "text":
            continue
        measurement = measure_text_block(
            element["text"],
            font_family_key=element["font_family_key"],
            font_weight=element["font_weight"],
            font_size_px=element["font_size_px"],
            line_height_px=element["line_height_px"],
            max_width_px=element["box"]["width"],
            max_lines=element["max_lines"],
            box_height_px=element["box"]["height"],
            registry=registry,
        )
        if not measurement.fits:
            raise TextOverflowError(
                f"patched element {element['id']!r} no longer fits: "
                f"widest line {measurement.widest_line_px:.1f}px of "
                f"{element['box']['width']}px, needs "
                f"{measurement.required_height_px}px of {element['box']['height']}px"
            )
    return spec


def _read_field(element: Mapping[str, Any], field: str) -> object:
    if field.startswith("box."):
        box = element.get("box", {})
        return box.get(field.split(".", 1)[1]) if isinstance(box, Mapping) else None
    return element.get(field)


def _binding_evidence(
    keyword_text: str,
    element_ids: Sequence[str],
    *,
    title: str,
    subtitle: str | None,
    brief_binding: Mapping[str, Any] | None,
    template_id: str,
) -> str:
    parts: list[str] = []
    if brief_binding:
        interpretation = _as_text(brief_binding.get("interpretation"))
        if interpretation:
            parts.append(interpretation)
        evidence = brief_binding.get("visual_evidence")
        if isinstance(evidence, list):
            parts.extend(str(item) for item in evidence if _as_text(item))
        elif _as_text(evidence):
            parts.append(str(evidence))
    if keyword_text and keyword_text in title:
        parts.append("关键词出现在标题文案中，并通过标题层级强调")
    if keyword_text and subtitle and keyword_text in subtitle:
        parts.append("关键词出现在副标题文案中")
    if not parts:
        parts.append(f"在 {template_id} 中由 {'、'.join(element_ids)} 承载")
    return "；".join(dict.fromkeys(parts))


def _brief_bindings(brief: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw = brief.get("keyword_bindings")
    if not isinstance(raw, list):
        return {}
    return {
        item["keyword_id"]: item
        for item in raw
        if isinstance(item, Mapping) and isinstance(item.get("keyword_id"), str)
    }


def plan_poster_spec(
    brief: Mapping[str, Any],
    *,
    request: OriginalRequest,
    template_id: str | None = None,
    previous_spec: Mapping[str, Any] | None = None,
    revision_action: RevisionAction | None = None,
    registry: FontRegistry | None = None,
    font_family_key: str | None = None,
) -> dict[str, Any]:
    """Build a validated PosterSpec from the brief; pure and testable."""

    if not isinstance(brief, Mapping):
        raise LayoutContractError("creative brief must be a JSON object")
    active_registry = registry or default_font_registry()
    family_key = font_family_key or default_family_key()
    title, subtitle = _brief_copy(brief)
    _check_keywords(brief, request)
    brief_version = _brief_version(brief)
    palette = resolve_palette(brief)
    brief_bindings = _brief_bindings(brief)

    previous = previous_spec if isinstance(previous_spec, Mapping) else None
    action = revision_action
    if previous is not None and action not in (None, RevisionAction.revise_layout):
        raise LayoutContractError(
            f"layout planner cannot apply revision action {action!r}"
        )

    patched: dict[str, Any] | None = None
    if previous is not None and action == RevisionAction.revise_layout:
        patch = brief.get("layout_patch")
        if isinstance(patch, Mapping):
            patched = _apply_layout_patch(previous, patch, registry=active_registry)

    if patched is not None:
        patched["run_id"] = request.run_id
        patched["spec_version"] = int(previous.get("spec_version", 1)) + 1
        patched["based_on"] = {"brief_version": brief_version}
        patched["design_tokens"] = dict(patched.get("design_tokens", {}))
        patched["design_tokens"]["palette"] = palette
        validate_poster_spec(patched)
        geometry = check_layout_geometry(patched, registry=active_registry)
        if not geometry["ok"]:
            raise LayoutContractError(
                "patched layout fails geometry checks: "
                + "; ".join(str(item["code"]) for item in geometry["issues"])
            )
        return patched

    template, pinned = _select_template(brief, request, template_id)
    if previous is not None and action == RevisionAction.revise_layout:
        order = list(template_ids())
        previous_template = previous.get("template_id")
        if pinned:
            raise LayoutContractError(
                "revise_layout with a pinned template requires a layout_patch; "
                "refusing to silently re-plan the same layout"
            )
        if previous_template in order:
            template = get_template(order[(order.index(previous_template) + 1) % len(order)])

    background = background_color(template, palette)
    color_by_role: dict[str, str] = {}
    for slot in template.text_slots:
        if slot.color_role not in color_by_role:
            color_by_role[slot.color_role] = slot_text_color(slot, background, palette)

    elements: list[dict[str, Any]] = [build_hero_element(template)]
    for slot in template.text_slots:
        copy = title if slot.copy_ref == "title" else subtitle
        if not copy:
            continue
        elements.append(
            fit_text_slot(
                slot,
                copy,
                color=color_by_role[slot.color_role],
                font_family_key=family_key,
                registry=active_registry,
            )
        )
    elements.sort(key=lambda item: int(item["z"]))

    prompt, negative = _visual_prompt(brief, request)
    present_ids = {str(element["id"]) for element in elements}
    overlap_pairs = [
        list(pair)
        for pair in template.allowed_overlaps
        if pair[0] in present_ids and pair[1] in present_ids
    ]
    keywords = request.keywords
    bindings = []
    for keyword in keywords:
        element_ids = ["hero"]
        if keyword.text and keyword.text in title:
            element_ids.append("title")
        if keyword.text and subtitle and keyword.text in subtitle:
            element_ids.append("subtitle")
        bindings.append(
            {
                "keyword_id": keyword.keyword_id,
                "element_ids": element_ids,
                "layout_evidence": _binding_evidence(
                    keyword.text,
                    element_ids,
                    title=title,
                    subtitle=subtitle,
                    brief_binding=brief_bindings.get(keyword.keyword_id),
                    template_id=template.template_id,
                ),
            }
        )

    spec_version = 1
    if previous is not None:
        spec_version = int(previous.get("spec_version", 1)) + 1
    spec: dict[str, Any] = {
        "schema_version": "0.1.0",
        "run_id": request.run_id,
        "spec_version": spec_version,
        "based_on": {"brief_version": brief_version},
        "canvas": {"width": 720, "height": 1280, "color_mode": "RGB"},
        "template_id": template.template_id,
        "background": {"color": background},
        "design_tokens": {
            "palette": palette,
            "font_family_key": family_key,
            "safe_margin_px": 36,
        },
        "asset_plan": [
            hero_asset_plan(
                template,
                prompt=prompt,
                negative_prompt=negative,
                keyword_ids=[keyword.keyword_id for keyword in keywords],
            )
        ],
        "elements": elements,
        "keyword_element_bindings": bindings,
        "allowed_overlaps": overlap_pairs,
        "render_policy": {
            "text_overflow": "error",
            "missing_font": "error",
            "output_format": "PNG",
        },
    }
    validate_poster_spec(spec)
    geometry = check_layout_geometry(spec, registry=active_registry)
    if not geometry["ok"]:
        codes = "; ".join(
            f"{item['code']}({','.join(item['element_ids'])})"
            for item in geometry["issues"]
            if item["blocking"]
        )
        raise LayoutContractError(f"planned layout fails geometry checks: {codes}")
    coverage = check_planned_keyword_coverage(request, spec)
    if not coverage["ok"]:
        raise LayoutContractError(
            "planned layout does not cover every original keyword: "
            + ", ".join(
                str(item["keyword_id"])
                for item in coverage["keywords"]
                if item["status"] != "planned"
            )
        )
    return spec


def _previous_layout_spec(context: AgentContext) -> dict[str, Any] | None:
    memory = getattr(context, "memory", None)
    if memory is None:
        return None
    try:
        snapshot = memory.context()
    except Exception:
        return None
    if not isinstance(snapshot, Mapping):
        return None
    artifacts = snapshot.get("artifacts")
    if not isinstance(artifacts, list):
        return None
    candidates = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, Mapping)
        and isinstance(artifact.get("reference"), Mapping)
        and artifact["reference"].get("role") == AgentRole.layout_planner.value
        and isinstance(artifact.get("content"), Mapping)
    ]
    if not candidates:
        return None
    target = context.metadata.revision_target
    if target is not None:
        matching = [
            artifact
            for artifact in candidates
            if artifact["reference"].get("artifact_id") == target.artifact_id
            and artifact["reference"].get("revision") == target.revision
        ]
        if matching:
            return dict(matching[-1]["content"])
    return dict(candidates[-1]["content"])


class LayoutPlannerAgent(Agent):
    """Deterministic planner; replace ``plan_poster_spec`` to add an LLM."""

    def __init__(
        self,
        *,
        template_id: str | None = None,
        registry: FontRegistry | None = None,
        font_family_key: str | None = None,
    ) -> None:
        self._template_id = template_id
        self._registry = registry
        self._font_family_key = font_family_key

    async def run(self, inputs: AgentInput, context: AgentContext) -> AgentOutput:
        if inputs.content_format.name != BRIEF_FORMAT.name:
            raise LayoutContractError(
                "LayoutPlannerAgent requires a poster.creative-brief document"
            )
        if not isinstance(inputs.content, Mapping):
            raise LayoutContractError("creative brief must be a JSON object")
        action = context.metadata.revision_action
        previous = _previous_layout_spec(context) if action == RevisionAction.revise_layout else None
        spec = plan_poster_spec(
            inputs.content,
            request=context.original_request,
            template_id=self._template_id,
            previous_spec=previous,
            revision_action=action,
            registry=self._registry,
            font_family_key=self._font_family_key,
        )
        if spec["run_id"] != inputs.metadata.identity.run_id:
            raise LayoutContractError("brief and execution run_id must match")
        return AgentOutput(content=spec, content_format=POSTER_SPEC_FORMAT)


def registration() -> AgentRegistration:
    return AgentRegistration(
        role=AgentRole.layout_planner,
        instance_factory=lambda config: LayoutPlannerAgent(),
        input_formats=frozenset({BRIEF_FORMAT, LEGACY_BRIEF_FORMAT}),
        output_formats=frozenset({POSTER_SPEC_FORMAT}),
        output_validator=validate_poster_spec,
    )


__all__ = [
    "BRIEF_FORMAT",
    "LAYOUT_BRIEF_FORMAT",
    "LEGACY_BRIEF_FORMAT",
    "POSTER_SPEC_FORMAT",
    "LayoutPlannerAgent",
    "describe_templates",
    "plan_poster_spec",
    "registration",
    "validate_poster_spec",
]





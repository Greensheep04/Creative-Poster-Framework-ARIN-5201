"""Bounded semantic composition using the existing measured text / image renderer.

No pixels are observed here: image-space placement is a generation intention,
never a claim that a generated subject or text-safe region has been detected.
"""
from __future__ import annotations

import copy
import re

from .quality_layout import QualityPlanningPolicy, common_spec, fit_copy, score_templates
from .layout_errors import LayoutContractError, TextOverflowError
from .layout_fonts import pick_text_color
from .layout_checks import check_layout_geometry
from .layout_spec import validate_poster_spec
from .types import CANVAS_HEIGHT, CANVAS_WIDTH


MIN_COLUMN_IMAGE_FRACTION = 0.32


class VisualPromptOverflowError(TextOverflowError):
    code = "VISUAL_PROMPT_OVERFLOW"


CATALOG_VARIANTS = {
    "image_first": "T1_IMAGE_FIRST",
    "full_bleed": "T2_FULL_BLEED",
    "side_by_side": "T3_ASYMMETRIC",
    "typography_led": "T4_BIG_TYPE",
}


def _narrative_contrast(raw):
    layout = raw.get("layout", {})
    composition = str(layout.get("composition", ""))
    marker = r"\bnarrative[-_ ]contrast\b|\bbefore\s*[/_–—-]\s*after\b|前后对照|对照叙事"
    negation = r"(?:\b(?:no|without|avoid|do not use)\s+(?:an?\s+)?|(?:避免|不做|不要|无需)\s*)$"
    return any(not re.search(negation, composition[:match.start()], re.I)
               for match in re.finditer(marker, composition, re.I))


def composition_intent(raw):
    layout = raw.get("layout", {})
    hint = str(layout.get("composition", "")).split(";", 1)[0].strip().lower()
    canonical = {"asymmetric_left": ("side_by_side", "left"),
                 "asymmetric_right": ("side_by_side", "right"),
                 "typography_led": ("typography_led", "center"),
                 "image_first": ("image_first", "center"), "framed": ("framed", "center")}
    if hint in canonical:
        relation, side = canonical[hint]
        return relation, side, "A1 explicit relationship hint: " + hint
    if _narrative_contrast(raw):
        return "image_first", "center", "A1 requests a before/after narrative in one artwork"
    text = " ".join(str(layout.get(k, "")) for k in ("composition", "reading_order", "typography")).lower()
    if re.search(r"typography[-_ ]led|type[- ]led|文字主导|大字主导|标题主导", text):
        return "typography_led", "center", "A1 makes typography the primary visual"
    if re.search(r"asymmetr|side.by.side|left.right split|horizontal split|左右分|非对称|image[_ ](?:left|right)", text):
        left = bool(re.search(r"asymmetric_left|image[_ ]left|image on (?:the )?left|图(?:像)?在左|左图右文", text))
        return "side_by_side", "left" if left else "right", "A1 requests lateral separation / asymmetry"
    if re.search(r"image[-_ ]first|先看|图像先行|图在上|bottom.up|start at (?:the )?bottom|ascending|上升|由下向上", text):
        return "image_first", "center", "A1 prioritizes the visual / upward narrative"
    return "legacy", "center", "No supported explicit relationship; measured legacy fallback"


def compile_composition_prompt(raw, relation, side, *, count_tokens=None):
    direction = raw.get("visual_direction", raw.get("visual_requirements", {}))
    subject = str(direction.get("main_subject", "")).strip()
    if not subject:
        subject = str(raw.get("intent", {}).get("core_message", "A single clear visual subject"))
    constraints = {
        "side_by_side": f"Complete subject, uncluttered background, visual emphasis toward the {side}.",
        "typography_led": "One iconic subject, simple background, complete silhouette.",
        "image_first": "Clear foreground subject, complete scene.",
        "framed": "Complete subject, uncluttered background.",
        "legacy": "Clear subject, uncluttered background.",
    }[relation]
    if _narrative_contrast(raw):
        constraints += " One continuous scene with a visible transition."
    # Copy lives outside the source in these safe compositions. Do not reserve
    # half the source merely because its image slot sits on half the canvas.
    if relation == "image_first" and re.search(r"upward|ascending|bottom.up|由下向上|上升", str(raw.get("layout", {})), re.I):
        constraints += " Upward visual movement."
    style = str(direction.get("style", "")).strip()
    fragments = [subject.rstrip("."), constraints, style, "No text or watermark."]
    deferred = []
    def prompt():
        return ". ".join(s.strip(". ") for s in fragments if s) + "."
    if count_tokens and max(count_tokens(prompt())) > 77:
        fragments[2] = ""
        deferred.append("style_deferred_for_required_subject_and_composition")
    if count_tokens and max(count_tokens(prompt())) > 77:
        fragments[1] = ""
        deferred.append("layout_preference_deferred_for_required_subject")
    if count_tokens and max(count_tokens(prompt())) > 77:
        raise VisualPromptOverflowError("Essential visual subject exceeds SDXL token budget; simplify main_subject, preserve poster copy")
    return prompt(), constraints if fragments[1] else "", deferred


def composition_negative(raw, positive):
    """Do not put a required entity into a qualified negative phrase.

    CLIP negative conditioning does not reliably scope 'faint' / 'close-up'.
    Qualified appearance/view preferences are deferred with a receipt; an
    unqualified prohibition of a required subject is an explicit conflict.
    """
    def words(value):
        stop = {"a", "an", "the", "of", "in", "on", "with", "and", "or", "no", "not", "visible", "clear", "subject", "background"}
        return {w.rstrip("s") for w in re.findall(r"[a-z]+", value.lower()) if w not in stop and len(w) > 2}
    direction = raw.get("visual_direction", raw.get("visual_requirements", {}))
    required = words(str(direction.get("main_subject", "")))
    avoid = direction.get("avoid", raw.get("avoid", []))
    negatives, deferred = ["text", "logo", "watermark", "extra letters"], []
    for item in avoid:
        if words(item) & required:
            if re.search(r"faint|implied|close.up|macro|giant|oversized|overgrown|multiple|instead of|overly|cartoon|exaggerat|beyond|except|other than", item, re.I):
                deferred.append({"source": item, "reason": "Qualified preference would negate a required positive entity"})
                continue
            raise LayoutContractError("Required visual subject conflicts with an absolute avoid item: " + item)
        negatives.append(item)
    return ", ".join(dict.fromkeys(negatives)), deferred


class CompositionPlanningPolicy(QualityPlanningPolicy):
    def __init__(self, *, typography=None, count_tokens=None, source_size_for=None):
        super().__init__(typography=typography)
        self.count_tokens = count_tokens
        self.source_size_for = source_size_for

    def _image_area(self, spec):
        if self.source_size_for is None:
            return {}
        hero = spec["elements"][0]
        width, height = hero["box"]["width"], hero["box"]["height"]
        source_width, source_height = self.source_size_for((width, height))
        content_width, content_height = width, height
        if hero["fit"] == "contain":
            scale = min(width / source_width, height / source_height)
            content_width = max(1, round(source_width * scale))
            content_height = max(1, round(source_height * scale))
        return {
            "planned_source_size": [source_width, source_height],
            "planned_image_content_fraction": content_width * content_height / (CANVAS_WIDTH * CANVAS_HEIGHT),
        }

    def plan(self, raw, request, registry, family, *, pinned_template=None):
        relation, side, reason = composition_intent(raw)
        attempts = []
        candidates = []
        if pinned_template is None and relation in {"side_by_side", "typography_led"}:
            candidates = [(relation, n) for n in range(2)]
            headline = raw["copy"]["headline"]
            if relation == "side_by_side" and re.search(r"[\u4e00-\u9fff]", headline) and len(headline) <= 20:
                candidates.append(("typography_led", 0))
        # No unverified image overlay is introduced by automatic selection.
        ranked = score_templates(raw)
        safe_ranked = next(item["template_id"] for item in ranked if item["template_id"] != "full_bleed_v1")
        fallback = {"image_first": "split_stack_v1", "framed": "framed_hero_v1"}.get(relation, safe_ranked)
        spec = evidence = None
        area_rejected = False
        for mode, variant in candidates:
            if mode == "typography_led" and area_rejected:
                continue
            try:
                candidate, candidate_evidence = self._semantic(raw, request, registry, family, mode, side, variant)
                area = self._image_area(candidate)
                if (mode == "side_by_side" and area and
                        area["planned_image_content_fraction"] < MIN_COLUMN_IMAGE_FRACTION):
                    attempts.append({"relation": mode, "variant": variant, "status": "rejected",
                                     "rejection_code": "image_area_below_floor",
                                     "minimum_image_content_fraction": MIN_COLUMN_IMAGE_FRACTION, **area})
                    area_rejected = True
                    continue
                spec, evidence = candidate, candidate_evidence
                attempts.append({"relation": mode, "variant": variant, "status": "selected"})
                break
            except (TextOverflowError, LayoutContractError) as error:
                attempts.append({"relation": mode, "variant": variant, "status": "rejected", "reason": str(error)})
        if spec is None:
            if candidates and relation == "side_by_side" and pinned_template is None:
                fallback = "split_stack_v1"
            spec, evidence = super().plan(raw, request, registry, family, pinned_template=pinned_template or fallback)
            evidence["relation"] = {"framed_hero_v1": "framed", "split_stack_v1": "image_first", "full_bleed_v1": "full_bleed"}[spec["template_id"]]
        # Preserve explicit legacy full-bleed behavior, including its caller's
        # responsibility for pixel-based review; do not invent safe-image facts.
        actual_relation = evidence["relation"]
        if actual_relation == "image_first" and pinned_template is None:
            texts = [e for e in spec["elements"] if e["type"] == "text"]
            top = min(e["box"]["y"] for e in texts)
            bottom = max(e["box"]["y"] + e["box"]["height"] for e in texts)
            shift = 1244 - bottom
            for element in texts:
                element["box"]["y"] += shift
            hero = spec["elements"][0]
            hero["box"]["height"] = top + shift - 48
            spec["asset_plan"][0]["target_size"] = {k: hero["box"][k] for k in ("width", "height")}
            validate_poster_spec(spec)
            checks = check_layout_geometry(spec, registry=registry)
            if not checks["ok"]:
                raise LayoutContractError(str(checks["issues"]))
            evidence["image_height_basis"] = "Canvas minus measured approved copy and 48px gap"
        evidence.update(self._image_area(spec))
        if spec["template_id"] != "full_bleed_v1":
            prompt, constraints, deferred = compile_composition_prompt(raw, actual_relation, side, count_tokens=self.count_tokens)
            spec["asset_plan"][0]["prompt"] = prompt
        else:
            constraints, deferred = "Explicit full-bleed; pixel safety requires QC", []
        negative, negative_receipts = composition_negative(raw, spec["asset_plan"][0]["prompt"])
        spec["asset_plan"][0]["negative_prompt"] = negative
        narrative = _narrative_contrast(raw)
        overlay_requested = any(item["reasons"] for item in ranked if item["template_id"] == "full_bleed_v1")
        if pinned_template is None and (narrative or overlay_requested):
            evidence.update(deferred_overlay_template="T5_NARRATIVE_CONTRAST" if narrative else "T2_FULL_BLEED",
                            overlay_deferral_reason="No source-bound contrast or clearance observation")
        evidence.update(
            planner="composition-aware-v3", intended_relation=relation, prompt_relation=actual_relation, image_side=side,
            catalog_selection=CATALOG_VARIANTS.get(actual_relation),
            selection_reason=reason, candidate_attempts=attempts,
            candidate_count=len(attempts) + (0 if candidates and attempts[-1]["status"] == "selected" else 1),
            prompt_layout_constraints=constraints, deferred_prompt_preferences=deferred,
            deferred_negative_preferences=negative_receipts,
            actual_subject_positions="unknown", actual_contrast="unknown",
        )
        return spec, evidence

    def _text(self, identifier, value, box, *, registry, family, title=False, large=False):
        sizes = (112, 100, 88, 80) if large else ((64, 60, 56, 52) if title else (30, 28, 26, 24))
        fitted = None
        for candidate in sizes:
            try:
                measured = fit_copy(
                    value, family=family, weight=700 if title else 400, registry=registry,
                    sizes=(candidate,), width=box["width"], max_lines=4 if large else (3 if title else 6),
                    leading=1.12 if title else 1.32, max_height=box["height"],
                )
            except TextOverflowError:
                continue
            lines = measured[3]
            stranded_word = title and any(line.strip().lower() in {"the", "a", "an", "of", "in", "and", "to", "or"} for line in lines)
            stranded_cjk = len(lines) > 1 and len(lines[-1].strip()) == 1 and bool(re.search(r"[\u4e00-\u9fff]", lines[-1]))
            if not stranded_word and not stranded_cjk:
                fitted = measured
                break
        if fitted is None:
            raise TextOverflowError("Approved copy cannot achieve a readable balanced text block")
        size, leading, height, lines = fitted
        return {"id": identifier, "type": "text", "z": 2 if title else 3,
                "box": {**box, "height": height}, "copy_ref": identifier, "text": value,
                "font_family_key": family, "font_weight": 700 if title else 400,
                "font_size_px": size, "line_height_px": leading,
                "max_lines": len(lines), "align": "left", "color": "#000000"}

    def _semantic(self, raw, request, registry, family, relation, side, variant):
        spec = common_spec(raw, request, family)
        spec.update(schema_version="0.2.0", template_id="semantic_layout_v1")
        color = pick_text_color(spec["background"]["color"], spec["design_tokens"]["palette"])
        if relation == "side_by_side":
            width = (244, 280)[variant]
            hero_width = 612 - width
            text_x = 36 if side == "right" else 684 - width
            hero_x = 72 + width if side == "right" else 36
            text_y, text_height = 168, 1040
            hero_box = {"x": hero_x, "y": 108, "width": hero_width, "height": 1100}
        else:
            text_x, text_y, width, text_height = 48, 64, (624, 588)[variant], 610
            hero_box = None
        texts = []
        fields = [("title", "headline"), ("subtitle", "subtitle"), ("cta", "call_to_action")]
        y = text_y
        for identifier, key in fields:
            value = raw["copy"].get(key, "")
            if not value:
                continue
            remaining = text_y + text_height - y
            if remaining <= 0:
                raise TextOverflowError("Approved copy has no remaining safe space")
            item = self._text(identifier, value, {"x": text_x, "y": y, "width": width, "height": remaining},
                              registry=registry, family=family, title=identifier == "title",
                              large=relation == "typography_led" and identifier == "title")
            item["color"] = color
            texts.append(item)
            y += item["box"]["height"] + (32 if identifier == "title" else 24)
        if relation == "typography_led":
            hero_y = max(580, y + 20)
            if hero_y > 1000:
                raise TextOverflowError("Typography-led composition cannot retain a meaningful visual")
            hero_box = {"x": 120 if variant == 0 else 48, "y": hero_y, "width": 552, "height": 1244 - hero_y}
        hero = copy.deepcopy(spec["elements"][0])
        hero.update(box=hero_box, fit="contain", focus_point_norm=[.5, .5])
        spec["elements"] = [hero] + texts
        spec["asset_plan"][0]["target_size"] = {k: hero_box[k] for k in ("width", "height")}
        spec["allowed_overlaps"] = []
        validate_poster_spec(spec)
        checks = check_layout_geometry(spec, registry=registry)
        if not checks["ok"]:
            raise LayoutContractError(str(checks["issues"]))
        return spec, {"relation": relation, "approved_fit": hero["fit"], "crop_requires_review": False, "unsupported_preferences": [],
                      "basis": "A1 relationship and measured approved copy; no inferred image observations"}


def repair_composition(previous, issues, registry):
    """Target deterministic text defects, preserving the entire generation plan.

    A transcription mismatch alone does not establish a font or asset defect.
    Unknown defects deliberately keep pixels unchanged so the workflow stops.
    """
    spec = copy.deepcopy(previous)
    targets = []
    elements = {e["id"]: e for e in spec["elements"]}
    for issue in issues:
        if issue.get("severity") not in {"major", "blocking"}:
            continue
        item = elements.get(issue.get("element_id"))
        if not item or item["type"] != "text":
            continue
        code = issue.get("code", "").lower()
        if "contrast" in code:
            # Only a solid background has a deterministic color remedy.
            if any(item["id"] in pair for pair in spec["allowed_overlaps"]):
                continue
            item["color"] = pick_text_color(spec["background"]["color"])
            targets.append(item["id"])
    validate_poster_spec(spec)
    checks = check_layout_geometry(spec, registry=registry)
    if not checks["ok"]:
        raise LayoutContractError(str(checks["issues"]))
    return spec, {"planner": "composition-aware-v1", "repair_targets": sorted(set(targets)),
                  "repair": "Targeted text repair; unchanged source generation plan",
                  "actual_subject_positions": "unknown", "actual_contrast": "unknown",
                  "unresolved_codes": [i.get("code") for i in issues if i.get("element_id") not in targets]}

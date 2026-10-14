"""Render complete posters from preserved CD copy, temporary layouts and SDXL assets."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cd_sdxl_preview import load_cases
from poster_visual_producer.contracts import CreativeBrief, PosterSpec, TextElement, validate_pair, StageError
from poster_visual_producer.fonts import FontRegistry
from poster_visual_producer.imaging import decode_image, encode_png
from poster_visual_producer.renderer import measure_text_block, preflight, render_poster


def load_inputs(root):
    rows = load_cases(root / "examples/creative-director-samples",
                      root / "examples/visual/cd-preview-prompts.json")
    profiles = json.loads((root / "examples/visual/cd-poster-layouts.json").read_text(encoding="utf-8"))["profiles"]
    return [(row, profiles[row["id"]]) for row in rows]


def font_registry():
    return FontRegistry.from_config(json.loads(
        (ROOT / "examples/visual/fonts.json").read_text(encoding="utf-8")), ROOT)


def build_documents(row, profile, fonts=None):
    fonts = fonts or font_registry()
    cd = row["cd_output"]
    elements, copy = [], {}
    for field in ("headline", "supporting_copy"):
        lines = profile[field + "_lines"]
        if " ".join(lines) != cd.get(field, ""):
            raise ValueError(f"Layout copy differs from CD {field}: {row['id']}")
        is_headline = field == "headline"
        size = profile["headline_size" if is_headline else "supporting_size"]
        step = profile["headline_step" if is_headline else "supporting_step"]
        start_y = profile["headline_y" if is_headline else "supporting_y"]
        for index, line in enumerate(lines):
            identifier = f"{field}_{index+1}"
            copy[identifier] = {"text": line, "origin": "original_cd_copy_typeset_segment", "must_preserve": True}
            item = {"id": identifier, "type": "text", "z": 10 + len(elements),
                    "box": {"x": 36, "y": start_y + index * step, "width": 648, "height": step - 4},
                    "copy_ref": identifier, "text": line, "font_family_key": "noto_sans_sc",
                    "font_weight": 700 if is_headline else 400,
                    "font_size_px": size, "line_height_px": step, "max_lines": 1,
                    "align": profile["headline_align"] if is_headline else "center",
                    "color": profile["text_color"]}
            # Fit each authored line using the same real font as final rendering.
            for font_size in range(size, 11, -1):
                item["font_size_px"] = font_size
                try:
                    measure_text_block(TextElement.model_validate(item), fonts)
                    break
                except StageError as error:
                    if error.code != "TEXT_OVERFLOW":
                        raise
            else:
                raise ValueError("CD copy cannot fit the temporary layout")
            elements.append(item)
    bindings = cd["keyword_mapping"]
    keywords = [f"k{i+1}" for i in range(len(bindings))]
    run_id = "poster_" + row["id"]
    brief = {
        "schema_version": "0.1.0", "run_id": run_id, "brief_version": 1,
        "request_ref": row["source"],
        "intent": {"concept": cd["concept"], "visual_direction": cd["visual_direction"],
                   "style": cd["style"], "headline": cd["headline"],
                   "supporting_copy": cd.get("supporting_copy", "")},
        "keyword_bindings": [{"keyword_id": identifier, "interpretation": item["keyword"],
                              "visual_evidence": [item["representation"]], "text_alone_sufficient": False}
                             for identifier, item in zip(keywords, bindings)],
        "copy": copy,
        "visual_requirements": {"must_include": cd["must_include"], "must_avoid": cd["must_avoid"]},
        "layout_intent": {"temporary_layout": True, "original_visual_direction": cd["visual_direction"]},
        "assumptions": [{"field": "layout", "value": "temporary authored layout",
                         "reason": "A2 is not yet available; headline/supporting copy are unchanged."}],
        "reference_notes": row["conversion_notes"], "open_issues": [],
    }
    plate = {"id": "poster_plate", "type": "image", "z": 0,
             "box": {"x": 0, "y": 0, "width": 720, "height": 1280},
             "asset_id": "poster_plate", "fit": "contain", "focus_point_norm": [0.5, 0.5]}
    spec = {
        "schema_version": "0.1.0", "run_id": run_id, "spec_version": 1,
        "based_on": {"brief_version": 1}, "canvas": {"width": 720, "height": 1280, "color_mode": "RGB"},
        "template_id": "full_bleed_v1", "background": {"color": profile["background"]},
        "design_tokens": {"palette": profile["palette"], "font_family_key": "noto_sans_sc", "safe_margin_px": 36},
        "asset_plan": [{"asset_id": "poster_plate", "kind": "generated_image",
                        "prompt": row["prompt"], "negative_prompt": row["negative_prompt"],
                        "target_size": {"width": 720, "height": 1280},
                        "subject_safe_region_norm": [0, 0, 1, 1], "required_keyword_ids": keywords}],
        "elements": [plate, *elements],
        "keyword_element_bindings": [{"keyword_id": key, "element_ids": ["poster_plate"]} for key in keywords],
        "allowed_overlaps": [["poster_plate", item["id"]] for item in elements],
        "render_policy": {"text_overflow": "error", "missing_font": "error", "output_format": "PNG"},
    }
    validate_pair(PosterSpec.model_validate(spec), CreativeBrief.model_validate(brief), keywords)
    return brief, spec


def make_plate(source, profile, layouts):
    im = Image.new("RGBA", (720, 1280), profile["background"])
    x, y, w, h = profile["image_box"]
    photo = ImageOps.fit(decode_image(source), (w, h), Image.Resampling.LANCZOS)
    im.alpha_composite(photo, (x, y))
    decoration = profile["decoration"]
    if decoration in {"sunlight", "bridge", "neon"}:
        mask = Image.new("RGBA", (720, 1280))
        draw = ImageDraw.Draw(mask)
        rgb = tuple(bytes.fromhex(profile["background"][1:]))
        for dy in range(100):
            draw.line((0, y + dy, 720, y + dy), fill=(*rgb, round(255 * (1 - dy / 100))))
        for yy in range(1060, 1280):
            draw.line((0, yy, 720, yy), fill=(*rgb, round(255 * min(1, (yy - 1060) / 140))))
        im.alpha_composite(mask)
    draw = ImageDraw.Draw(im)
    accent = profile["accent"]
    if decoration == "watercolor":
        draw.line((36, 242, 156, 242), fill=accent, width=3)
        draw.line((24, 270, 24, 1128), fill=accent, width=1)
    elif decoration == "coral":
        draw.rounded_rectangle((24, 256, 695, 1144), radius=10, outline=accent, width=2)
        draw.line((306, 236, 414, 236), fill=accent, width=2)
    elif decoration == "clay":
        draw.line((100, 300, 620, 300), fill=profile["text_color"], width=1)
        draw.ellipse((354, 294, 366, 306), fill=profile["text_color"])
    elif decoration in {"bridge", "sunlight"}:
        draw.line((36, y - 20, 160, y - 20), fill=accent, width=2)
    elif decoration == "neon":
        for offset in (0, 12, 24):
            draw.line((480 + offset, 42, 683, 42), fill=accent, width=1)
            draw.line((683, 42 + offset, 683, 224), fill=accent, width=1)
    effect = profile["text_effect"]
    if effect in {"glow", "shadow"}:
        layer = Image.new("RGBA", im.size)
        painter = ImageDraw.Draw(layer)
        for key, layout in layouts.items():
            if not key.startswith("headline"):
                continue
            for placement in layout.placements:
                px, py = placement["draw_xy"]
                painter.text((px, py + (3 if effect == "shadow" else 0)), placement["text"],
                             font=layout.font, fill=accent if effect == "glow" else (0, 0, 0, 140), anchor="lt")
        im.alpha_composite(layer.filter(ImageFilter.GaussianBlur(7 if effect == "glow" else 3)))
    return im.convert("RGB")


def render_case(row, profile, source, fonts, output):
    brief, spec_dict = build_documents(row, profile, fonts)
    spec = PosterSpec.model_validate(spec_dict)
    layouts = preflight(spec, fonts)
    plate = make_plate(source, profile, layouts)
    plate_bytes = encode_png(plate)
    rendered = render_poster(spec, {"poster_plate": plate_bytes}, fonts)
    output.mkdir(parents=True, exist_ok=False)
    (output / "source.png").write_bytes(source)
    (output / "plate.png").write_bytes(plate_bytes)
    (output / "poster.png").write_bytes(rendered.png)
    for filename, content in (("original-cd.json", row["cd_output"]), ("creative-brief.json", brief),
                              ("poster-spec.json", spec_dict), ("temporary-layout.json", profile),
                              ("render_manifest.json", rendered.manifest)):
        (output / filename).write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"id": row["id"], "model": row["model"], "theme": row["theme"],
            "headline": row["cd_output"]["headline"], "supporting_copy": row["cd_output"].get("supporting_copy", ""),
            "status": "rendered", "poster": row["id"] + "/poster.png", "canvas": [720, 1280],
            "source_image_sha256": hashlib.sha256(source).hexdigest(),
            "poster_sha256": rendered.manifest["output_sha256"],
            "cd_source_sha256": row["source_sha256"], "layout_source": "temporary_authored_layout",
            "asset_processing": {"source_size": list(decode_image(source).size),
                                 "image_box": profile["image_box"], "resize_mode": "aspect_preserving_fit",
                                 "decoration": profile["decoration"], "text_effect": profile["text_effect"],
                                 "plate_contains_derived_glow_or_shadow": profile["text_effect"] != "none"},
            "visual_quality_evaluated": False}


def make_comparison(output, rows):
    sheet = Image.new("RGB", (1200, 1500), "#EEF2F6")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=16)
    for index, row in enumerate(rows):
        x, y = index % 3 * 400, index // 3 * 750
        draw.text((x + 14, y + 12), row["theme"], font=font, fill="#172C40")
        draw.text((x + 14, y + 38), row["model"], font=font, fill="#172C40")
        with Image.open(output / row["poster"]) as poster:
            sheet.paste(poster.resize((372, 661), Image.Resampling.LANCZOS), (x + 14, y + 70))
    sheet.save(output / "contact-sheet.png")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = load_inputs(ROOT)
    fonts = font_registry()
    # Check every layout, source hash and SDXL/CD association before writing outputs.
    asset_report = json.loads((args.asset_run / "report.json").read_text(encoding="utf-8"))
    assets = {row["id"]: row for row in asset_report["cases"]}
    sources = {}
    for row, profile in inputs:
        build_documents(row, profile, fonts)
        record = assets[row["id"]]
        path = (args.asset_run / record["image"]).resolve()
        if not path.is_relative_to(args.asset_run.resolve()):
            raise ValueError("Asset path escapes its run directory")
        source = path.read_bytes()
        if hashlib.sha256(source).hexdigest() != record["image_sha256"] or record["source_sha256"] != row["source_sha256"]:
            raise ValueError("Asset/source association differs from the recorded CD generation")
        decode_image(source)
        sources[row["id"]] = source
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"scope": "complete_posters_from_cd_with_temporary_layouts", "status": "rendering",
              "additional_image_operations": 0, "original_asset_run": str(args.asset_run.resolve()),
              "copy_policy": "exact_CD_copy_with_typesetting_only", "cases": [], "visual_quality_evaluated": False}
    report_path = args.output / "report.json"
    try:
        for row, profile in inputs:
            result = render_case(row, profile, sources[row["id"]], fonts, args.output / row["id"])
            report["cases"].append(result)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        make_comparison(args.output, report["cases"])
        report["status"] = "rendered"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error_message=str(error))
        raise
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "rendered", "posters": len(report["cases"]), "additional_image_operations": 0,
                      "output": str(args.output.resolve())}))


if __name__ == "__main__":
    main()

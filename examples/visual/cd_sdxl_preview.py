"""Preview authored CD-to-prompt conversions with the existing local SDXL backend."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import textwrap
import time

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from poster_visual_producer.backends import create_generator
from poster_visual_producer.generators import GenerationRequest
from poster_visual_producer.imaging import decode_image


def load_cases(sample_root: Path, manifest_path: Path) -> list[dict]:
    """Read authored prompts only when their original CD source hashes match."""
    sample_root = sample_root.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    result, identifiers = [], set()
    for entry in manifest["cases"]:
        source = (sample_root / entry["source"]).resolve()
        if not source.is_relative_to(sample_root):
            raise ValueError("CD source path escapes sample directory")
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry["source_sha256"]:
            raise ValueError(f"CD source hash differs: {entry['source']}")
        identifier = entry["id"]
        if not re.fullmatch(r"[a-z0-9_-]+", identifier) or identifier in identifiers:
            raise ValueError("Case identifiers must be unique safe file names")
        identifiers.add(identifier)
        for field in ("prompt", "negative_prompt"):
            if not isinstance(entry[field], str) or not entry[field].strip():
                raise ValueError(f"Missing {field} in {identifier}")
        cd = json.loads(data)
        if not isinstance(cd, dict) or not isinstance(cd.get("concept"), str):
            raise ValueError("CD output needs an object with a concept")
        result.append({
            **entry, "cd_output": cd,
            "conversion_mode": manifest.get("conversion_mode"),
            "source_commit": manifest.get("source_commit"),
        })
    if not result:
        raise ValueError("No CD cases selected")
    return result


def preflight(cases: list[dict], tokenizers: tuple, seed: int) -> list[dict]:
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("Seed must be an unsigned 32-bit integer")
    rows = []
    for case in cases:
        counts = {}
        for field in ("prompt", "negative_prompt"):
            counts[field] = []
            for tokenizer in tokenizers:
                count = len(tokenizer(case[field], truncation=False)["input_ids"])
                if count > tokenizer.model_max_length:
                    raise ValueError(f"{case['id']} {field} exceeds token limit: {count}")
                counts[field].append(count)
        request = GenerationRequest(
            asset_id=case["id"], prompt=case["prompt"],
            negative_prompt=case["negative_prompt"], size=(768, 1024),
            seed=seed, seed_reason="specified", adaptations=(), repair_constraints=(),
        )
        rows.append({**case, "token_counts": counts, "request": request.as_json(),
                     "status": "prepared", "visual_quality_evaluated": False})
    return rows


def write_report(output: Path, report: dict) -> None:
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def contact_sheet(output: Path, rows: list[dict]) -> None:
    columns, cell_w, cell_h = 3, 400, 660
    sheet = Image.new("RGB", (columns * cell_w, ((len(rows) + 2) // 3) * cell_h), "#eef2f6")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=17)
    small = ImageFont.load_default(size=14)
    for index, row in enumerate(rows):
        x, y = index % columns * cell_w, index // columns * cell_h
        draw.text((x + 12, y + 12), row["theme"], font=font, fill="#172c40")
        draw.text((x + 12, y + 38), row["model"], font=font, fill="#172c40")
        with Image.open(output / row["image"]) as image:
            preview = image.convert("RGB").resize((372, 496), Image.Resampling.LANCZOS)
            sheet.paste(preview, (x + 14, y + 68))
        headline = row["cd_output"].get("headline", "")
        lines = textwrap.wrap("CD headline: " + headline, width=42)
        for line_index, line in enumerate(lines[:3]):
            draw.text((x + 12, y + 579 + line_index * 20), line, font=small, fill="#172c40")
        draw.text((x + 12, y + 639), f"SDXL | seed {row['request']['seed']} | visual asset preview", font=small, fill="#536579")
    sheet.save(output / "contact-sheet.png")


async def run_preview(cases, generator, tokenizers, output, *, seed=42, dry_run=False):
    # Validate the whole batch before writing or submitting any GPU request.
    rows = preflight(cases, tokenizers, seed)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "status": "dry_run" if dry_run else "running",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "visual_assets_only_without_A2_or_QC",
        "conversion_mode": "authored_visual_prompts_from_cd",
        "visual_quality_evaluated": False,
        "image_operations": 0, "cases": rows,
    }
    if generator is not None:
        capabilities = generator.capabilities if hasattr(generator, "capabilities") else None
        if capabilities:
            if (768, 1024) not in capabilities.sizes or not capabilities.supports_seed:
                raise ValueError("Backend must support portrait size and explicit seed")
            report["backend"] = {"provider": capabilities.provider, "model": capabilities.model,
                                 "weights_version": capabilities.weights_version,
                                 "parameters": dict(capabilities.generation_parameters)}
    write_report(output, report)
    if dry_run:
        return report
    for row in rows:
        started = time.perf_counter()
        report["image_operations"] += 1
        row["status"] = "generating"
        write_report(output, report)
        try:
            request = GenerationRequest(
                row["id"], row["prompt"], row["negative_prompt"], (768, 1024), seed,
                "specified", (), (),
            )
            image = await generator.generate(request)
            decoded = decode_image(image.data)
            if decoded.size != (768, 1024):
                raise ValueError("Generated image has an unexpected size")
            row["image"] = row["id"] + ".png"
            decoded.convert("RGB").save(output / row["image"], "PNG")
            row.update(status="generated", elapsed_seconds=round(time.perf_counter() - started, 3),
                       request_id=image.request_id, usage=image.usage, provenance=image.provenance,
                       image_sha256=hashlib.sha256((output / row["image"]).read_bytes()).hexdigest())
        except Exception as error:
            report["status"] = "failed"
            row.update(status="failed", error_type=type(error).__name__,
                       elapsed_seconds=round(time.perf_counter() - started, 3))
            write_report(output, report)
            raise
        write_report(output, report)
        print(f"Generated {row['id']} in {row['elapsed_seconds']}s", flush=True)
    contact_sheet(output, rows)
    report["status"] = "generated"
    report["contact_sheet"] = "contact-sheet.png"
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    write_report(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=ROOT / "examples/creative-director-samples")
    parser.add_argument("--prompts", type=Path, default=ROOT / "examples/visual/cd-preview-prompts.json")
    parser.add_argument("--backend-config", type=Path, default=ROOT / "examples/visual/backend-sdxl.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.backend_config.read_text(encoding="utf-8"))
    if config.get("kind") != "sdxl":
        parser.error("This comparison uses the local SDXL backend only")
    generator = create_generator(config)
    from transformers import CLIPTokenizer
    tokenizers = tuple(CLIPTokenizer.from_pretrained(
        str(generator.config.model_dir / name), local_files_only=True
    ) for name in ("tokenizer", "tokenizer_2"))
    report = asyncio.run(run_preview(
        load_cases(args.samples, args.prompts), generator, tokenizers, args.output,
        seed=args.seed, dry_run=args.dry_run,
    ))
    print(json.dumps({"status": report["status"], "cases": len(report["cases"]),
                      "image_operations": report["image_operations"],
                      "output": str(args.output.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()

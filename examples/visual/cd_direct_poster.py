"""Full-CD SDXL poster generation, retaining raw model text for comparison with Pillow."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cd_sdxl_preview import load_cases
from poster_visual_producer.backends import create_generator, verify_weights
from poster_visual_producer.imaging import decode_image, encode_png


def load_source_cases(root):
    loaded = load_cases(root / "examples/creative-director-samples",
                        root / "examples/visual/cd-preview-prompts.json")
    # The earlier shortened visual prompts are not used in this experiment.
    fields = ("id", "model", "theme", "source", "source_sha256", "source_commit", "cd_output")
    return [{key: row[key] for key in fields} for row in loaded]


def build_prompt(cd):
    lines = [
        "Create one finished portrait art poster, integrating visual artwork and readable English typography.",
        "Print this exact main headline prominently: " + json.dumps(cd["headline"], ensure_ascii=False) + ".",
    ]
    if cd.get("supporting_copy"):
        lines.append("Print this exact supporting slogan, smaller than the headline: " +
                     json.dumps(cd["supporting_copy"], ensure_ascii=False) + ".")
    else:
        lines.append("Use the headline only; do not add a supporting slogan or other copy.")
    lines.extend([
        "Creative concept: " + cd["concept"],
        "Visual composition: " + cd["visual_direction"],
        "Express each original keyword through the following representations:",
    ])
    for mapping in cd["keyword_mapping"]:
        lines.append(mapping["keyword"] + ": " + mapping["representation"])
    for field, value in cd["style"].items():
        lines.append("Artistic " + field + ": " + (", ".join(value) if isinstance(value, list) else value))
    lines.append("Required content and constraints: " + "; ".join(cd["must_include"]))
    lines.append("Avoid the following, as specified in the source brief: " + "; ".join(cd["must_avoid"]))
    lines.append("Create a coherent poster with expressive typography, clear reading hierarchy and generous margins. "
                 "The quoted headline and slogan are the only permitted words. Do not invent statistics, brands or organizations.")
    if "Copy for headline only" in cd["must_avoid"]:
        lines.append("The source contains an ambiguous headline-only item in its avoid list; "
                     "keep the required headline and add no supporting copy.")
    incompatible = {"Copy for headline only", "Foreign text"}
    avoid = [value for value in cd["must_avoid"] if value not in incompatible]
    negative = ", ".join(["misspelled words", "illegible typography", "random extra words", "watermark", "brand logos", *avoid])
    return "\n".join(lines), negative


def chunk_tokens(tokenizer, text, count=None):
    ids = tokenizer(text, add_special_tokens=False, truncation=False, verbose=False)["input_ids"]
    window = tokenizer.model_max_length
    capacity = window - 2
    if capacity <= 0:
        raise ValueError("Tokenizer needs room for content and two special tokens")
    required = max(1, math.ceil(len(ids) / capacity))
    count = required if count is None else count
    if count < required or count > 32:
        raise ValueError("Invalid chunk count; prompt must not be truncated")
    chunks, content_counts = [], []
    for index in range(count):
        content = ids[index * capacity:(index + 1) * capacity]
        chunks.append([tokenizer.bos_token_id, *content, tokenizer.eos_token_id] +
                      [tokenizer.pad_token_id] * (capacity - len(content)))
        content_counts.append(len(content))
    return {"chunks": chunks, "content_counts": content_counts, "token_count": len(ids), "original_ids": ids}


def token_plan(tokenizers, prompt, negative):
    original = [[chunk_tokens(tokenizer, text) for tokenizer in tokenizers]
                for text in (prompt, negative)]
    count = max(len(part["chunks"]) for field in original for part in field)
    plans = [[chunk_tokens(tokenizer, text, count) for tokenizer in tokenizers]
             for text in (prompt, negative)]
    metadata = {
        "encoding": "concatenated_CLIP_chunks_with_token_weighted_pooled_mean",
        "positive_token_counts": [part["token_count"] for part in plans[0]],
        "negative_token_counts": [part["token_count"] for part in plans[1]],
        "chunks": count,
        "tokens_dropped": 0,
        "content_counts": {"positive": [part["content_counts"] for part in plans[0]],
                           "negative": [part["content_counts"] for part in plans[1]]},
    }
    return plans, metadata


def native_analysis(tokenizers, prompt, negative):
    counts = [[chunk_tokens(tokenizer, text)["token_count"] for tokenizer in tokenizers]
              for text in (prompt, negative)]
    for field in counts:
        if any(count + 2 > tokenizer.model_max_length for count, tokenizer in zip(field, tokenizers)):
            raise ValueError("Prompt exceeds native token limit; no silent truncation")
    return {"encoding": "native_single_CLIP_window", "positive_token_counts": counts[0],
            "negative_token_counts": counts[1], "chunks": 1, "tokens_dropped": 0}


def apply_native_prompt(row, manifest_path):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    prompt = manifest["prompts"][row["id"]]
    for field in ("headline", "supporting_copy"):
        if row["cd_output"].get(field) and row["cd_output"][field] not in prompt:
            raise ValueError("Native prompt changes or omits official CD copy")
    row.update(prompt=prompt, negative_prompt=build_prompt(row["cd_output"])[1],
               conversion_mode=manifest["conversion_mode"], conversion_note=manifest["note"])


def encode_conditioning(pipe, prompt, negative, device):
    """Concatenate penultimate-layer chunk contexts, matching SDXL's two encoder widths."""
    import torch
    plans, metadata = token_plan((pipe.tokenizer, pipe.tokenizer_2), prompt, negative)
    values = []
    for field in plans:
        contexts, pooled = [], None
        for index, (part, encoder) in enumerate(zip(field, (pipe.text_encoder, pipe.text_encoder_2))):
            ids = torch.tensor(part["chunks"], dtype=torch.long, device=device)
            encoded = encoder(ids, output_hidden_states=True)
            hidden = encoded.hidden_states[-2]
            contexts.append(hidden.reshape(1, -1, hidden.shape[-1]))
            if index == 1:
                weights = torch.tensor(part["content_counts"], dtype=encoded.text_embeds.dtype, device=device)
                if not weights.sum().item():
                    weights[0] = 1
                weights = weights / weights.sum()
                pooled = (encoded.text_embeds * weights[:, None]).sum(dim=0, keepdim=True)
        values.append((torch.cat(contexts, dim=-1), pooled))
    metadata["embedding_shape"] = list(values[0][0].shape)
    return {"prompt_embeds": values[0][0], "pooled_prompt_embeds": values[0][1],
            "negative_prompt_embeds": values[1][0], "negative_pooled_prompt_embeds": values[1][1]}, metadata


def prepare_cases(root, reference_run):
    original = load_source_cases(root)
    reference = json.loads((reference_run / "report.json").read_text(encoding="utf-8"))
    if reference.get("status") != "rendered":
        raise ValueError("Pillow reference run must be fully rendered")
    records = {row["id"]: row for row in reference["cases"]}
    prepared = []
    for row in original:
        record = records[row["id"]]
        if record["cd_source_sha256"] != row["source_sha256"]:
            raise ValueError("Pillow reference uses a different CD source")
        path = (reference_run / record["poster"]).resolve()
        if not path.is_relative_to(reference_run.resolve()):
            raise ValueError("Pillow reference path escapes its run")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != record["poster_sha256"]:
            raise ValueError("Pillow reference image hash differs")
        if decode_image(data).size != (720, 1280):
            raise ValueError("Pillow reference dimensions differ")
        prompt, negative = build_prompt(row["cd_output"])
        prepared.append({**row, "prompt": prompt, "negative_prompt": negative,
                         "reference_bytes": data, "reference_path": str(path),
                         "reference_png_sha256": record["poster_sha256"]})
    return prepared


class DirectPosterBackend:
    def __init__(self, config, encoding="chunked"):
        if config.cpu_offload:
            raise ValueError("This explicit chunked-encoding experiment requires cpu_offload=false")
        self.config, self.pipe, self.encoding = config, None, encoding
        from transformers import CLIPTokenizer
        self.tokenizers = tuple(CLIPTokenizer.from_pretrained(str(config.model_dir / name), local_files_only=True)
                                for name in ("tokenizer", "tokenizer_2"))
        self.description = {"model": config.model_id, "weights_revision": config.revision,
                            "steps": config.steps, "guidance_scale": config.guidance_scale,
                            "dtype": "float16", "canvas": [720, 1280], "cpu_offload": False, "encoding": encoding}

    def analyze(self, prompt, negative):
        if self.encoding == "native":
            return native_analysis(self.tokenizers, prompt, negative)
        return token_plan(self.tokenizers, prompt, negative)[1]

    def load(self):
        verify_weights(self.config)
        import torch
        from diffusers import StableDiffusionXLPipeline
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is required; no backend fallback")
        self.pipe = StableDiffusionXLPipeline.from_pretrained(
            str(self.config.model_dir), torch_dtype=torch.float16, variant="fp16",
            use_safetensors=True, local_files_only=True, add_watermarker=False,
        )
        self.pipe.enable_vae_slicing()
        self.pipe.to("cuda")

    def generate(self, prompt, negative, seed):
        import torch
        import diffusers
        if self.pipe is None:
            self.load()
        torch.cuda.reset_peak_memory_stats()
        with torch.inference_mode():
            if self.encoding == "native":
                metadata = self.analyze(prompt, negative)
                embeddings = {"prompt": prompt, "negative_prompt": negative}
            else:
                embeddings, metadata = encode_conditioning(self.pipe, prompt, negative, "cuda")
            image = self.pipe(
                **embeddings, width=720, height=1280,
                generator=torch.Generator(device="cpu").manual_seed(seed),
                num_inference_steps=self.config.steps, guidance_scale=self.config.guidance_scale,
            ).images[0]
        metadata.update(actual_seed=seed, torch_version=str(torch.__version__), diffusers_version=diffusers.__version__,
                        peak_allocated_vram_bytes=torch.cuda.max_memory_allocated(),
                        model_text_retouched=False, api_cost_usd=0)
        return encode_png(image.convert("RGB")), metadata


def comparison_label(row):
    if row.get("conditioning", {}).get("encoding") == "native_single_CLIP_window":
        return "Native SDXL: compact CD + exact copy"
    return "Chunked SDXL: full CD fields"


def comparison_pair(output, row):
    font = ImageFont.load_default(size=17)
    small = ImageFont.load_default(size=14)
    sheet = Image.new("RGB", (900, 875), "#EEF2F6")
    draw = ImageDraw.Draw(sheet)
    draw.text((15, 10), row["theme"] + " | " + row["model"], font=font, fill="#172C40")
    draw.text((15, 40), comparison_label(row), font=font, fill="#172C40")
    draw.text((465, 40), "SDXL asset + Pillow copy/layout", font=font, fill="#172C40")
    for x, filename in ((15, "direct.png"), (465, "pillow.png")):
        with Image.open(output / row["id"] / filename) as image:
            sheet.paste(image.convert("RGB").resize((420, 747), Image.Resampling.LANCZOS), (x, 75))
    draw.text((15, 840), "Target headline: " + row["cd_output"]["headline"], font=small, fill="#172C40")
    sheet.save(output / row["id"] / "comparison.png")
    return sheet


def make_comparisons(output, rows):
    pairs = [comparison_pair(output, row) for row in rows]
    overview = Image.new("RGB", (1800, 875 * math.ceil(len(pairs) / 2)), "#EEF2F6")
    for index, pair in enumerate(pairs):
        overview.paste(pair, (index % 2 * 900, index // 2 * 875))
    overview.save(output / "comparison-all.png")
    for name, marker in (("ocean", "ocean-"), ("tradition", "tradition-")):
        selected = [pair for row, pair in zip(rows, pairs) if row["id"].startswith(marker)]
        if selected:
            sheet = Image.new("RGB", (900, len(selected) * 875), "#EEF2F6")
            for index, pair in enumerate(selected):
                sheet.paste(pair, (0, index * 875))
            sheet.save(output / (name + "-comparison.png"))


def make_three_way(output, native_rows, full_run):
    full_report = json.loads((full_run / "report.json").read_text(encoding="utf-8"))
    full_rows = {row["id"]: row for row in full_report["cases"]}
    prepared = []
    for row in native_rows:
        other = full_rows[row["id"]]
        if other["source_sha256"] != row["source_sha256"] or other["reference_png_sha256"] != row["reference_png_sha256"]:
            raise ValueError("Three-way comparison uses different source or reference")
        image_path = (full_run / other["direct_png"]).resolve()
        if not image_path.is_relative_to(full_run.resolve()):
            raise ValueError("Full-CD comparison path escapes its run")
        data = image_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != other["direct_sha256"]:
            raise ValueError("Full-CD comparison image hash differs")
        prepared.append((row, image_path))
    pairs = []
    font, small = ImageFont.load_default(size=16), ImageFont.load_default(size=14)
    for row, full_path in prepared:
        sheet = Image.new("RGB", (1320, 840), "#EEF2F6")
        draw = ImageDraw.Draw(sheet)
        draw.text((15, 10), row["theme"] + " | " + row["model"], font=font, fill="#172C40")
        panels = [(15, full_path, "Full CD: chunked SDXL"),
                  (455, output / row["id"] / "direct.png", "Compact CD: native SDXL"),
                  (895, output / row["id"] / "pillow.png", "SDXL asset + Pillow composition")]
        for x, path, label in panels:
            draw.text((x, 40), label, font=font, fill="#172C40")
            with Image.open(path) as image:
                sheet.paste(image.convert("RGB").resize((400, 711), Image.Resampling.LANCZOS), (x, 76))
        draw.text((15, 811), "Target headline: " + row["cd_output"]["headline"], font=small, fill="#172C40")
        sheet.save(output / row["id"] / "comparison-three-way.png")
        pairs.append((row, sheet))
    for name in ("ocean", "tradition"):
        selected = [sheet for row, sheet in pairs if row["id"].startswith(name + "-")]
        if selected:
            group = Image.new("RGB", (1320, 840 * len(selected)), "#EEF2F6")
            for index, sheet in enumerate(selected):
                group.paste(sheet, (0, 840 * index))
            group.save(output / (name + "-three-way.png"))


def run_experiment(cases, backend, output, *, seed=42, dry_run=False):
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("Seed must be an unsigned 32-bit integer")
    rows = [{**{key: value for key, value in case.items() if key != "reference_bytes"},
             "conditioning": backend.analyze(case["prompt"], case["negative_prompt"]),
             "status": "prepared", "model_text_retouched": False} for case in cases]
    output.mkdir(parents=True, exist_ok=False)
    report = {"status": "dry_run" if dry_run else "running", "scope": "full_CD_direct_SDXL_vs_Pillow",
              "started_at_utc": datetime.now(timezone.utc).isoformat(), "image_operations": 0,
              "backend": getattr(backend, "description", {}), "seed": seed,
              "conversion_mode": cases[0].get("conversion_mode", "complete_CD_fields_to_explicit_poster_prompt"),
              "encoding_note": ("Native single CLIP window; visual descriptions compressed, exact copy retained."
                                if getattr(backend, "encoding", None) == "native" else
                                "Experimental chunked CLIP context concatenation; pooled mean weighted by content tokens."),
              "comparison_note": "Same SDXL weights/steps/guidance/seed; prior Pillow source was generated at 768x1024, "
                                 "new direct poster at 720x1280. Prior layout was manually authored. This is an exploratory comparison.",
              "cases": rows}

    def save():
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    save()
    if dry_run:
        return report
    try:
        for row, case in zip(rows, cases):
            folder = output / row["id"]
            folder.mkdir()
            (folder / "prompt.txt").write_text(row["prompt"], encoding="utf-8")
            (folder / "negative-prompt.txt").write_text(row["negative_prompt"], encoding="utf-8")
            (folder / "original-cd.json").write_text(json.dumps(row["cd_output"], ensure_ascii=False, indent=2), encoding="utf-8")
            (folder / "pillow.png").write_bytes(case["reference_bytes"])
            started = time.perf_counter()
            row["status"] = "generating"
            report["image_operations"] += 1
            save()
            data, generation = backend.generate(row["prompt"], row["negative_prompt"], seed)
            if decode_image(data).size != (720, 1280):
                raise ValueError("Direct SDXL poster size differs from the comparison canvas")
            # Preserve the native generated image bytes. Never draw replacement text on it.
            (folder / "direct.png").write_bytes(data)
            row.update(status="generated", elapsed_seconds=round(time.perf_counter() - started, 3),
                       generation=generation, direct_sha256=hashlib.sha256(data).hexdigest(),
                       direct_png=row["id"] + "/direct.png", pillow_png=row["id"] + "/pillow.png")
            save()
            print(f"Generated direct poster {row['id']} in {row['elapsed_seconds']}s", flush=True)
        make_comparisons(output, rows)
        report.update(status="generated", finished_at_utc=datetime.now(timezone.utc).isoformat())
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error_message=str(error))
        if "row" in locals() and row["status"] == "generating":
            row["status"] = "failed"
        raise
    finally:
        save()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-run", type=Path, required=True)
    parser.add_argument("--backend-config", type=Path, default=ROOT / "examples/visual/backend-sdxl.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--encoding", choices=("chunked", "native"), default="chunked")
    parser.add_argument("--native-prompts", type=Path, default=ROOT / "examples/visual/cd-direct-native-prompts.json")
    parser.add_argument("--rebuild-comparisons", action="store_true")
    parser.add_argument("--full-cd-run", type=Path)
    args = parser.parse_args()
    if args.rebuild_comparisons:
        report_path = args.output / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report["status"] != "generated":
            raise ValueError("Only completed image runs can rebuild comparisons")
        for row in report["cases"]:
            for field, filename in (("direct_sha256", "direct.png"), ("reference_png_sha256", "pillow.png")):
                data = (args.output / row["id"] / filename).read_bytes()
                if hashlib.sha256(data).hexdigest() != row[field]:
                    raise ValueError("Comparison input image changed")
        make_comparisons(args.output, report["cases"])
        if args.full_cd_run:
            make_three_way(args.output, report["cases"], args.full_cd_run)
            report["three_way_full_cd_run"] = str(args.full_cd_run.resolve())
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Comparison sheets rebuilt; image operations added: 0")
        return
    cases = prepare_cases(ROOT, args.reference_run)
    if args.encoding == "native":
        for row in cases:
            apply_native_prompt(row, args.native_prompts)
    config = json.loads(args.backend_config.read_text(encoding="utf-8"))
    if config.get("kind") != "sdxl":
        parser.error("This comparison requires the local SDXL model")
    backend = DirectPosterBackend(create_generator(config).config, args.encoding)
    report = run_experiment(cases, backend, args.output, seed=args.seed, dry_run=args.dry_run)
    print(json.dumps({"status": report["status"], "image_operations": report["image_operations"],
                      "cases": len(report["cases"]), "output": str(args.output.resolve())}))


if __name__ == "__main__":
    main()

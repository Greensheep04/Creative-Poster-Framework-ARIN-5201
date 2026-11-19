"""Write inspectable acceptance artifacts; all visual content is explicitly offline."""

from pathlib import Path
import asyncio, hashlib, json, shutil, sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from examples.visual.workflow_demo import run_demo
from poster_framework import ArtifactPurpose
from PIL import Image, ImageDraw, ImageFont


async def main():
    preview = ROOT / "runs/visual-producer-preview"
    preview.mkdir(parents=True, exist_ok=True)
    rows = []
    cases = [
        ("chinese", "initial", "zh"),
        ("title-move", "revise_layout", "zh"),
        ("asset-repair", "regenerate_asset", "zh"),
        ("rerender", "rerender", "zh"),
        ("english", "initial", "en"),
    ]
    for name, scenario, language in cases:
        root = ROOT / "runs/acceptance" / name
        result, summary = await run_demo(root, scenario=scenario, language=language)
        folder = preview / name
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(summary["poster"], folder / "poster.png")
        candidates = [
            r for r in result.artifacts if r.purpose == ArtifactPurpose.candidate
        ]
        first_path = root / Path(candidates[0].uri.removeprefix("runs/"))
        shutil.copyfile(first_path, folder / "before.png")
        visual = result.visual.content
        manifest_path = root / result.run_id / visual["render_manifest_ref"]
        shutil.copyfile(manifest_path, folder / "render_manifest.json")
        (folder / "candidate.json").write_text(
            json.dumps(visual, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        first_image = Image.open(folder / "before.png")
        final_image = Image.open(folder / "poster.png")
        checks = {
            "canvas": final_image.size == (720, 1280),
            "real_png": final_image.format == "PNG",
            "source_mode": "offline_fixture",
        }
        if name in {"title-move", "rerender"}:
            checks["no_extra_generation"] = result.usage.image_operations == 1
            checks["hero_pixels_preserved"] = (
                first_image.crop((36, 260, 684, 1110)).tobytes()
                == final_image.crop((36, 260, 684, 1110)).tobytes()
            )
        if name == "rerender":
            checks["poster_bytes_identical"] = (folder / "before.png").read_bytes() == (
                folder / "poster.png"
            ).read_bytes()
        if name == "asset-repair":
            checks["one_extra_generation"] = result.usage.image_operations == 2
            checks["title_pixels_preserved"] = (
                first_image.crop((36, 64, 684, 216)).tobytes()
                == final_image.crop((36, 64, 684, 216)).tobytes()
            )
            checks["hero_changed"] = (
                first_image.crop((36, 260, 684, 1110)).tobytes()
                != final_image.crop((36, 260, 684, 1110)).tobytes()
            )
        assert all(v for v in checks.values() if isinstance(v, bool)), checks
        row = {**summary, "preview": str(folder / "poster.png"), "checks": checks}
        rows.append(row)
    (preview / "acceptance-report.json").write_text(
        json.dumps(
            {
                "source_mode": "offline_fixture",
                "visual_quality_evaluated": False,
                "cases": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    sheet = Image.new("RGB", (1280, 650), "#F1F5F7")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(
        str(ROOT / "resources/fonts/NotoSansCJKsc-Regular.otf"), 18
    )
    labels = [
        ("chinese", "中文基线 / 1 次"),
        ("title-move", "标题移动 / +0 次"),
        ("asset-repair", "素材返工 / +1 次"),
        ("english", "英文 / 1 次"),
    ]
    for i, (name, label) in enumerate(labels):
        draw.text((i * 320 + 16, 16), label, font=font, fill="#123C55")
        im = Image.open(preview / name / "poster.png").resize(
            (300, 533), Image.Resampling.LANCZOS
        )
        sheet.paste(im, (i * 320 + 10, 56))
    draw.text(
        (16, 608),
        "OFFLINE FIXTURE · 工程验收样张 · 未进行真实模型视觉质量评测",
        font=font,
        fill="#526B79",
    )
    sheet.save(preview / "contact-sheet.png")
    print(
        json.dumps(
            {
                "report": str(preview / "acceptance-report.json"),
                "contact_sheet": str(preview / "contact-sheet.png"),
                "cases": [
                    {
                        "case": row["scenario"],
                        "image_ops": row["usage"]["image_operations"],
                        "checks": row["checks"],
                    }
                    for row in rows
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())

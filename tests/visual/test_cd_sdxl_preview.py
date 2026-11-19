import asyncio
import importlib.util
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from poster_visual_producer.generators import GeneratedImage


ROOT = Path(__file__).resolve().parents[2]


def preview_module():
    path = ROOT / "examples/visual/cd_sdxl_preview.py"
    assert path.is_file(), "CD-to-SDXL preview entry point is missing"
    spec = importlib.util.spec_from_file_location("cd_preview", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Tokenizer:
    model_max_length = 77

    def __call__(self, text, **kwargs):
        return {"input_ids": [0] + text.split() + [1]}


class ImageBackend:
    async def generate(self, request):
        buffer = io.BytesIO()
        Image.new("RGB", request.size, "blue").save(buffer, "PNG")
        return GeneratedImage(buffer.getvalue(), provenance={"not_ai_generated": True})


def cases(module):
    return module.load_cases(
        ROOT / "examples/creative-director-samples",
        ROOT / "examples/visual/cd-preview-prompts.json",
    )


def test_dry_run_preserves_six_raw_cd_responses_and_makes_no_image(tmp_path):
    module = preview_module()
    result = asyncio.run(module.run_preview(
        cases(module), None, (Tokenizer(), Tokenizer()), tmp_path / "preview",
        dry_run=True,
    ))
    assert result["status"] == "dry_run"
    assert result["image_operations"] == 0
    assert len(result["cases"]) == 6
    raw = result["cases"][2]["cd_output"]
    assert raw["style"]["mood"] == ["Optimistic", "Bright", "Clean"]
    assert "supporting_copy" not in result["cases"][4]["cd_output"]
    assert not list((tmp_path / "preview").rglob("*.png"))


def test_changed_cd_source_rejected(tmp_path):
    module = preview_module()
    source = tmp_path / "samples"
    source.mkdir()
    (source / "changed.json").write_text('{"concept": "Changed"}')
    manifest = tmp_path / "prompts.json"
    manifest.write_text(json.dumps({"cases": [{
        "source": "changed.json", "source_sha256": "0" * 64,
        "id": "changed", "prompt": "ocean", "negative_prompt": "text"
    }]}))
    with pytest.raises(ValueError, match="source hash"):
        module.load_cases(source, manifest)


@pytest.mark.parametrize("field", ["prompt", "negative_prompt"])
def test_overlong_prompt_rejected_before_output_or_gpu_call(tmp_path, field):
    module = preview_module()
    loaded = cases(module)
    loaded[0][field] = "word " * 80
    with pytest.raises(ValueError, match="token limit"):
        asyncio.run(module.run_preview(
            loaded, None, (Tokenizer(), Tokenizer()), tmp_path / "preview"
        ))
    assert not (tmp_path / "preview").exists()


def test_generated_images_and_comparison_sheet_are_decodable(tmp_path):
    module = preview_module()
    result = asyncio.run(module.run_preview(
        cases(module), ImageBackend(), (Tokenizer(), Tokenizer()), tmp_path / "preview"
    ))
    assert result["status"] == "generated"
    assert result["image_operations"] == 6
    for row in result["cases"]:
        assert Image.open(tmp_path / "preview" / row["image"]).size == (768, 1024)
        assert row["request"]["seed"] == 42
        assert row["visual_quality_evaluated"] is False
    with Image.open(tmp_path / "preview/contact-sheet.png") as sheet:
        sheet.verify()


def test_generation_error_is_recorded_without_retry_or_success(tmp_path):
    module = preview_module()

    class BrokenBackend:
        async def generate(self, request):
            raise RuntimeError("GPU test failure")

    with pytest.raises(RuntimeError, match="GPU test failure"):
        asyncio.run(module.run_preview(
            cases(module), BrokenBackend(), (Tokenizer(), Tokenizer()), tmp_path / "preview"
        ))
    result = json.loads((tmp_path / "preview/report.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["image_operations"] == 1
    assert result["cases"][0]["status"] == "failed"
    assert not list((tmp_path / "preview").rglob("*.png"))

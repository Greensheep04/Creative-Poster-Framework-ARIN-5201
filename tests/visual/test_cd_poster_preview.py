import importlib.util
import io
import json
from pathlib import Path

from PIL import Image
import pytest

from poster_visual_producer.contracts import CreativeBrief, PosterSpec, TextElement, validate_pair
from poster_visual_producer.renderer import preflight
from .helpers import fonts

ROOT = Path(__file__).resolve().parents[2]


def module():
    path = ROOT / "examples/visual/cd_poster_preview.py"
    assert path.is_file(), "Full CD poster entry point is missing"
    spec = importlib.util.spec_from_file_location("cd_poster_preview", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_all_six_layouts_preserve_exact_headline_and_supporting_copy():
    m = module()
    rows = m.load_inputs(ROOT)
    assert len(rows) == 6
    for row, profile in rows:
        brief, spec = m.build_documents(row, profile)
        b, s = CreativeBrief.model_validate(brief), PosterSpec.model_validate(spec)
        validate_pair(s, b, [f"k{i+1}" for i in range(3)])
        preflight(s, fonts())
        for field in ("headline", "supporting_copy"):
            text = " ".join(e.text for e in s.elements
                            if isinstance(e, TextElement) and e.id.startswith(field))
            assert text == row["cd_output"].get(field, "")
        if row["id"].startswith("tradition"):
            assert not any(e.id.startswith("supporting_copy") for e in s.elements)


def test_poster_render_draws_the_copy_inside_canvas(tmp_path):
    m = module()
    row, profile = m.load_inputs(ROOT)[0]
    buffer = io.BytesIO()
    Image.new("RGB", (768, 1024), "#508ca2").save(buffer, "PNG")
    result = m.render_case(row, profile, buffer.getvalue(), fonts(), tmp_path / "poster")
    poster = Image.open(tmp_path / "poster/poster.png")
    plate = Image.open(tmp_path / "poster/plate.png")
    assert poster.size == (720, 1280)
    assert poster.crop((35, 50, 685, 225)).tobytes() != plate.crop((35, 50, 685, 225)).tobytes()
    assert result["headline"] == "Hope Grows from the Tide"
    assert result["supporting_copy"] == "Plastic meets the ocean — transformed into flight, not waste."
    manifest = json.loads((tmp_path / "poster/render_manifest.json").read_text(encoding="utf-8"))
    text_rows = [x for x in manifest["elements"] if x["type"] == "text"]
    assert len(text_rows) == 4
    assert all(0 <= x["actual_bounds"][0] < x["actual_bounds"][2] <= 720 for x in text_rows)
    assert all(0 <= x["actual_bounds"][1] < x["actual_bounds"][3] <= 1280 for x in text_rows)


def test_layout_cannot_silently_rewrite_cd_headline():
    m = module()
    row, profile = m.load_inputs(ROOT)[0]
    profile["headline_lines"][0] = "Invented headline"
    with pytest.raises(ValueError, match="copy differs"):
        m.build_documents(row, profile)

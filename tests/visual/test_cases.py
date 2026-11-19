import asyncio, importlib.util, json
import pytest
from PIL import Image
from poster_framework import OriginalRequest
from poster_visual_producer.generators import FixtureImageGenerator
from .helpers import fonts, png


def module():
    assert importlib.util.find_spec("poster_visual_producer.cases"), (
        "preset-case runner required"
    )
    from poster_visual_producer import cases

    return cases


def case(brief_data, spec_data):
    return dict(
        name="forest",
        keywords=["森林", "生命", "绿色"],
        brief=brief_data,
        spec=spec_data,
    )


def test_case_ids_map_to_actual_original_request(brief_data, spec_data):
    m = module()
    c = m.PresetCase.model_validate(case(brief_data, spec_data))
    req = OriginalRequest.from_keywords("preset-run", c.keywords)
    brief, spec = m.materialize_case(c, req)
    assert brief["run_id"] == spec["run_id"] == req.run_id
    assert [x["keyword_id"] for x in brief["keyword_bindings"]] == [
        k.keyword_id for k in req.keywords
    ]
    assert spec["asset_plan"][0]["required_keyword_ids"] == [
        k.keyword_id for k in req.keywords
    ]


def test_case_copy_mismatch_rejected(brief_data, spec_data):
    m = module()
    spec_data["elements"][1]["text"] = "偷改文案"
    c = m.PresetCase.model_validate(case(brief_data, spec_data))
    req = OriginalRequest.from_keywords("preset-run", c.keywords)
    with pytest.raises(ValueError):
        m.materialize_case(c, req)


def test_case_unbound_keyword_rejected(brief_data, spec_data):
    m = module()
    brief_data["keyword_bindings"].pop()
    c = m.PresetCase.model_validate(case(brief_data, spec_data))
    req = OriginalRequest.from_keywords("preset-run", c.keywords)
    with pytest.raises(ValueError):
        m.materialize_case(c, req)


def test_preset_case_real_framework_exports_and_png(tmp_path, brief_data, spec_data):
    m = module()
    c = m.PresetCase.model_validate(case(brief_data, spec_data))
    summary = asyncio.run(
        m.run_case(
            c,
            FixtureImageGenerator(lambda request: png()),
            fonts(),
            tmp_path,
            seed=None,
        )
    )
    assert summary["status"] == "rendered"
    assert summary["visual_quality_evaluated"] is False
    assert summary["usage"]["image_operations"] == 1
    assert (tmp_path / "a1-record.json").is_file() and (
        tmp_path / "a2-record.json"
    ).is_file()
    assert Image.open(tmp_path / "poster.png").size == (720, 1280)
    assert (
        json.loads((tmp_path / "candidate.json").read_text(encoding="utf-8"))["status"]
        == "rendered"
    )


def test_case_preview_preserves_jpeg_source_format(tmp_path, brief_data, spec_data):
    import io

    m = module()
    c = m.PresetCase.model_validate(case(brief_data, spec_data))
    data = io.BytesIO()
    Image.new("RGB", (800, 1000), "green").save(data, "JPEG")
    asyncio.run(
        m.run_case(
            c, FixtureImageGenerator(lambda request: data.getvalue()), fonts(), tmp_path
        )
    )
    assert (tmp_path / "hero_image-source.jpg").is_file()
    assert not (tmp_path / "hero_image-source.png").exists()
    assert Image.open(tmp_path / "hero_image-source.jpg").format == "JPEG"

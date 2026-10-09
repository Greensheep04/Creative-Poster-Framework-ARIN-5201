import copy, importlib.util
import pytest


def api():
    assert importlib.util.find_spec("poster_visual_producer"), (
        "Visual Producer package must implement document contracts"
    )
    from poster_visual_producer import contracts

    return contracts


def test_document_fixtures_roundtrip(spec_data, brief_data):
    c = api()
    spec, brief = (
        c.PosterSpec.model_validate(spec_data),
        c.CreativeBrief.model_validate(brief_data),
    )
    c.validate_pair(spec, brief, ["k1", "k2", "k3"])
    assert spec.model_dump(mode="json") == spec_data
    assert brief.copy_blocks["title"].text == "静海之蓝"


@pytest.mark.parametrize(
    "change",
    ["duplicate", "missing_asset", "wrong_canvas", "overflow", "script", "unsafe_id"],
)
def test_invalid_spec_rejected(spec_data, change):
    c = api()
    if change == "duplicate":
        spec_data["elements"].append(copy.deepcopy(spec_data["elements"][0]))
    if change == "missing_asset":
        spec_data["elements"][0]["asset_id"] = "missing"
    if change == "wrong_canvas":
        spec_data["canvas"]["width"] = 800
    if change == "overflow":
        spec_data["elements"][1]["box"]["x"] = 700
    if change == "script":
        spec_data["elements"][0]["script"] = "print(1)"
    if change == "unsafe_id":
        spec_data["asset_plan"][0]["asset_id"] = "../escape"
    with pytest.raises(ValueError):
        c.PosterSpec.model_validate(spec_data)


@pytest.mark.parametrize(
    "change,code",
    [
        ("copy", "CONSTRAINT_CONFLICT"),
        ("version", "STALE_VERSION"),
        ("keyword", "SCHEMA_INVALID"),
    ],
)
def test_pair_rejects_semantic_drift(spec_data, brief_data, change, code):
    c = api()
    if change == "copy":
        spec_data["elements"][1]["text"] = "不同标题"
    if change == "version":
        brief_data["brief_version"] = 2
    if change == "keyword":
        spec_data["keyword_element_bindings"][0]["keyword_id"] = "fake"
    with pytest.raises(c.StageError) as caught:
        c.validate_pair(
            c.PosterSpec.model_validate(spec_data),
            c.CreativeBrief.model_validate(brief_data),
            ["k1", "k2", "k3"],
        )
    assert caught.value.code == code

import importlib
from importlib.resources import files
import json
import re

import pytest


@pytest.fixture(scope="module")
def validate_brief():
    return importlib.import_module("creative.creative_director").validate_brief


@pytest.fixture
def brief():
    reference = files("creative") / "resources" / "sample-output.json"
    return json.loads(reference.read_text(encoding="utf-8"))["content"]


def field_parent(brief, path):
    *parents, field = path.split(".")
    for parent in parents:
        brief = brief[parent]
    return brief, field


def test_bundled_creative_director_brief_is_accepted_without_changes(validate_brief, brief):
    original = json.dumps(brief, ensure_ascii=False)

    assert validate_brief(brief) is None

    assert json.dumps(brief, ensure_ascii=False) == original


@pytest.mark.parametrize("value", [None, [], "{}", 42])
def test_brief_must_be_an_object(validate_brief, value):
    with pytest.raises(ValueError, match="brief"):
        validate_brief(value)


@pytest.mark.parametrize(
    "path",
    [
        "keywords",
        "core_message",
        "copy",
        "copy.headline",
        "copy.subtitle",
        "copy.call_to_action",
        "concept.central_idea",
        "visual_direction.supporting_elements",
        "layout.reading_order",
        "palette.colors",
        "avoid",
    ],
)
def test_missing_fields_identify_the_field(validate_brief, brief, path):
    parent, field = field_parent(brief, path)
    del parent[field]

    with pytest.raises(ValueError, match=re.escape(path)):
        validate_brief(brief)


@pytest.mark.parametrize(
    "path",
    [
        "unexpected",
        "concept.unexpected",
        "copy.unexpected",
        "visual_direction.unexpected",
        "layout.unexpected",
        "palette.unexpected",
    ],
)
def test_unknown_fields_identify_the_field(validate_brief, brief, path):
    parent, field = field_parent(brief, path)
    parent[field] = "unexpected value"

    with pytest.raises(ValueError, match=re.escape(path)):
        validate_brief(brief)


@pytest.mark.parametrize("path", ["concept", "copy", "visual_direction", "layout", "palette"])
@pytest.mark.parametrize("value", [None, [], "text"])
def test_nested_sections_must_be_objects(validate_brief, brief, path, value):
    brief[path] = value

    with pytest.raises(ValueError, match=re.escape(path)):
        validate_brief(brief)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("interpretation", None),
        ("objective", True),
        ("copy.headline", ["headline"]),
        ("copy.subtitle", 123),
        ("copy.call_to_action", None),
        ("visual_direction.style", {"style": "painted"}),
        ("palette.rationale", 1.5),
        ("keywords", "home"),
        ("visual_direction.supporting_elements", ("doorway",)),
        ("palette.colors", {"color": "green"}),
        ("avoid", None),
    ],
)
def test_fields_reject_wrong_types(validate_brief, brief, path, value):
    parent, field = field_parent(brief, path)
    parent[field] = value

    with pytest.raises(ValueError, match=re.escape(path)):
        validate_brief(brief)


@pytest.mark.parametrize(
    "path",
    [
        "interpretation",
        "audience",
        "objective",
        "core_message",
        "concept.title",
        "concept.central_idea",
        "concept.connection_to_keywords",
        "copy.headline",
        "visual_direction.main_subject",
        "visual_direction.setting",
        "visual_direction.visual_relationship",
        "visual_direction.style",
        "visual_direction.mood",
        "layout.focal_point",
        "layout.composition",
        "layout.reading_order",
        "layout.typography",
        "palette.rationale",
    ],
)
def test_required_text_cannot_be_blank(validate_brief, brief, path):
    parent, field = field_parent(brief, path)
    parent[field] = " \t\n"

    with pytest.raises(ValueError, match=re.escape(path)):
        validate_brief(brief)


@pytest.mark.parametrize(
    "path", ["keywords", "visual_direction.supporting_elements", "palette.colors", "avoid"]
)
@pytest.mark.parametrize("item", [None, 42, {}, "", " \t\n"])
def test_list_items_must_be_nonblank_strings(validate_brief, brief, path, item):
    parent, field = field_parent(brief, path)
    parent[field] = ["valid item", item]

    with pytest.raises(ValueError, match=re.escape(path)):
        validate_brief(brief)


@pytest.mark.parametrize("path", ["keywords", "palette.colors"])
def test_keywords_and_palette_require_at_least_one_item(validate_brief, brief, path):
    parent, field = field_parent(brief, path)
    parent[field] = []

    with pytest.raises(ValueError, match=re.escape(path)):
        validate_brief(brief)


def test_optional_text_and_lists_can_be_empty(validate_brief, brief):
    brief["copy"]["subtitle"] = ""
    brief["copy"]["call_to_action"] = ""
    brief["visual_direction"]["supporting_elements"] = []
    brief["avoid"] = []

    assert validate_brief(brief) is None

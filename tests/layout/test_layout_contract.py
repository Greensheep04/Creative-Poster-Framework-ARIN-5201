from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from layout_support import FONTS_AVAILABLE, make_brief, make_request
from poster_framework import (
    LayoutContractError,
    parse_poster_spec,
    plan_poster_spec,
    poster_spec_json_schema,
)

requires_fonts = pytest.mark.skipif(
    not FONTS_AVAILABLE, reason="no CJK-capable font installed"
)

# The shared JSON schema is owned by the visual-producer branch.  It is not
# vendored here to avoid a duplicate copy of A3's file; set POSTER_SPEC_SCHEMA
# to check A2 against it before that branch is merged.
_SCHEMA_CANDIDATES = (
    os.environ.get("POSTER_SPEC_SCHEMA"),
    "docs/schemas/PosterSpec.json",
    ".scratch/PosterSpec.json",
)
_CONSTRAINT_KEYS = (
    "type",
    "enum",
    "const",
    "pattern",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "maxItems",
    "minItems",
    "maxLength",
    "minLength",
)


def _shared_schema() -> dict[str, object]:
    for candidate in _SCHEMA_CANDIDATES:
        if candidate and Path(candidate).is_file():
            # utf-8-sig also accepts the UTF-8 BOM that Windows PowerShell 5.1
            # writes with ``Set-Content -Encoding UTF8``.
            return json.loads(Path(candidate).read_text(encoding="utf-8-sig"))
    pytest.skip(
        "shared PosterSpec.json not available; set POSTER_SPEC_SCHEMA to the "
        "visual-producer copy to run the contract comparison"
    )


def _resolve(node: object, root: dict[str, object]) -> object:
    hops = 0
    while isinstance(node, dict) and "$ref" in node:
        target: object = root
        for part in str(node["$ref"])[2:].split("/"):
            target = target[part]  # type: ignore[index]
        node = target
        hops += 1
        if hops > 16:
            raise AssertionError("cyclic schema reference")
    return node


def _diff(ours: object, theirs: object, root_a: dict[str, object], root_b: dict[str, object], path: str) -> list[str]:
    ours = _resolve(ours, root_a)
    theirs = _resolve(theirs, root_b)
    if not isinstance(ours, dict) or not isinstance(theirs, dict):
        return []
    problems: list[str] = []
    for key in _CONSTRAINT_KEYS:
        if theirs.get(key) != ours.get(key):
            problems.append(
                f"{path}: {key} ours={ours.get(key)!r} shared={theirs.get(key)!r}"
            )
    if set(theirs.get("required", [])) != set(ours.get("required", [])):
        problems.append(
            f"{path}: required ours={sorted(ours.get('required', []))} "
            f"shared={sorted(theirs.get('required', []))}"
        )
    for key in ("items", "additionalProperties"):
        if isinstance(theirs.get(key), dict) and isinstance(ours.get(key), dict):
            problems += _diff(ours[key], theirs[key], root_a, root_b, f"{path}.{key}")
    for key in ("oneOf", "anyOf"):
        if key in theirs:
            ours_list = ours.get(key, [])
            theirs_list = theirs[key]
            if len(ours_list) != len(theirs_list):
                problems.append(
                    f"{path}: {key} count ours={len(ours_list)} shared={len(theirs_list)}"
                )
                continue
            for index, (left, right) in enumerate(zip(ours_list, theirs_list)):
                problems += _diff(left, right, root_a, root_b, f"{path}.{key}[{index}]")
    for key in ("properties", "$defs"):
        if key in theirs:
            ours_map = ours.get(key, {})
            for name, node in theirs[key].items():
                if name not in ours_map:
                    problems.append(f"{path}.{key}: missing {name}")
                    continue
                problems += _diff(ours_map[name], node, root_a, root_b, f"{path}.{key}.{name}")
    return problems


def test_generated_json_schema_matches_shared_contract() -> None:
    shared = _shared_schema()
    ours = poster_spec_json_schema()
    problems = _diff(ours, shared, ours, shared, "")
    assert problems == [], "PosterSpec model drifted from docs/schemas/PosterSpec.json:\n" + "\n".join(problems)


@requires_fonts
def test_planned_spec_is_accepted_by_the_typed_contract() -> None:
    request = make_request()
    spec = plan_poster_spec(make_brief(), request=request)
    parsed = parse_poster_spec(spec)
    assert parsed.template_id == "framed_hero_v1"
    assert parsed.based_on.brief_version == 1
    assert parsed.run_id == request.run_id
    assert parsed.canvas.width == 720 and parsed.canvas.height == 1280
    assert [element.id for element in parsed.elements] == ["hero", "title", "subtitle"]


@requires_fonts
def test_every_required_top_level_field_is_enforced() -> None:
    spec = plan_poster_spec(make_brief(), request=make_request())
    for key in spec:
        broken = copy.deepcopy(spec)
        broken.pop(key)
        with pytest.raises(LayoutContractError):
            parse_poster_spec(broken)


@requires_fonts
def test_unknown_asset_reference_is_rejected() -> None:
    spec = plan_poster_spec(make_brief(), request=make_request())
    for element in spec["elements"]:
        if element["type"] == "image":
            element["asset_id"] = "missing_asset"
    with pytest.raises(LayoutContractError):
        parse_poster_spec(spec)


@requires_fonts
def test_out_of_canvas_box_is_rejected() -> None:
    spec = plan_poster_spec(make_brief(), request=make_request())
    for element in spec["elements"]:
        if element["id"] == "hero":
            element["box"]["x"] = 200
            element["box"]["width"] = 640
    with pytest.raises(LayoutContractError):
        parse_poster_spec(spec)


@requires_fonts
def test_duplicate_element_ids_are_rejected() -> None:
    spec = plan_poster_spec(make_brief(), request=make_request())
    spec["elements"][1]["id"] = "hero"
    with pytest.raises(LayoutContractError):
        parse_poster_spec(spec)


@requires_fonts
def test_undeclared_element_type_and_bad_color_are_rejected() -> None:
    spec = plan_poster_spec(make_brief(), request=make_request())
    spec["elements"][1]["color"] = "navy"
    with pytest.raises(LayoutContractError):
        parse_poster_spec(spec)

    spec = plan_poster_spec(make_brief(), request=make_request())
    spec["elements"].append({"id": "ribbon", "type": "shape", "z": 4})
    with pytest.raises(LayoutContractError):
        parse_poster_spec(spec)


@requires_fonts
def test_line_height_below_font_size_is_rejected() -> None:
    spec = plan_poster_spec(make_brief(), request=make_request())
    for element in spec["elements"]:
        if element["id"] == "title":
            element["line_height_px"] = element["font_size_px"] - 1
    with pytest.raises(LayoutContractError):
        parse_poster_spec(spec)


@requires_fonts
def test_allowed_overlaps_must_name_two_known_elements() -> None:
    spec = plan_poster_spec(make_brief(), request=make_request())
    spec["allowed_overlaps"] = [["hero", "ghost"]]
    with pytest.raises(LayoutContractError):
        parse_poster_spec(spec)

    spec["allowed_overlaps"] = [["hero", "hero"]]
    with pytest.raises(LayoutContractError):
        parse_poster_spec(spec)


@requires_fonts
def test_spec_round_trips_through_json() -> None:
    spec = plan_poster_spec(make_brief(), request=make_request())
    restored = json.loads(json.dumps(spec, ensure_ascii=False))
    parse_poster_spec(restored)

"""Run the A2 Layout Planner on one brief and print the measured result.

The example is deterministic and offline: it uses the installed CJK font, real
Pillow measurement and the framework's own checks.  It does not call any image
or language model.

    uv run python examples/layout_planner.py --root .scratch/layout-planner
    uv run python examples/layout_planner.py --template full_bleed_v1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Python 3.11 reads editable-install .pth files with the locale encoding, so a
# checkout under a non-ASCII path (for example a Chinese user name) loses the
# ``src`` entry and the package is not importable.  Keep the example runnable in
# both cases; ``uv run pytest`` is covered by the root conftest.py.
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from poster_framework import (
    AgentRole,
    ExecutionIdentity,
    ExecutionMode,
    FrameworkMetadata,
    OriginalRequest,
    check_layout_geometry,
    check_planned_keyword_coverage,
    default_font_registry,
    describe_templates,
    measure_text_block,
    plan_poster_spec,
    template_ids,
)


def _context(request: OriginalRequest) -> object:
    metadata = FrameworkMetadata(
        identity=ExecutionIdentity(
            run_id=request.run_id,
            role=AgentRole.layout_planner,
            invocation_id="layout-planner-example-invocation",
            execution_mode=ExecutionMode.real,
        ),
        original_request=request,
    )

    class Context:
        original_request = request
        identity = metadata.identity
        metadata = metadata
        memory = None

    return Context()


def _title_of(spec: dict[str, object]) -> dict[str, object]:
    elements = spec["elements"]
    assert isinstance(elements, list)
    return next(element for element in elements if element["id"] == "title")


async def run_example(
    template_id: str | None,
    title: str,
    subtitle: str | None,
) -> dict[str, object]:
    request = OriginalRequest.from_keywords(
        "layout-planner-example", ["海洋", "宁静", "蓝色"]
    )
    brief: dict[str, object] = {
        "copy": {"title": title},
        "palette": {
            "colors": [
                "deep ocean blue (#0B2A4C)",
                "faded turquoise (#A0D8E8)",
            ]
        },
        "visual_requirements": {
            "main_subject": "平静海面与开阔天空",
            "avoid": ["暴风雨", "密集人群"],
        },
    }
    if subtitle:
        brief["copy"] = {"title": title, "subtitle": subtitle}

    selected = [template_id] if template_id else list(template_ids())
    scenarios: list[dict[str, object]] = []
    for candidate in selected:
        spec = plan_poster_spec(brief, request=request, template_id=candidate)
        geometry = check_layout_geometry(spec)
        coverage = check_planned_keyword_coverage(request, spec)
        title_element = _title_of(spec)
        measurement = measure_text_block(
            str(title_element["text"]),
            font_family_key=str(title_element["font_family_key"]),
            font_weight=int(title_element["font_weight"]),
            font_size_px=int(title_element["font_size_px"]),
            line_height_px=int(title_element["line_height_px"]),
            max_width_px=int(title_element["box"]["width"]),  # type: ignore[index]
            max_lines=int(title_element["max_lines"]),
            box_height_px=int(title_element["box"]["height"]),  # type: ignore[index]
        )
        scenarios.append(
            {
                "template_id": spec["template_id"],
                "spec": spec,
                "checks": {
                    "geometry_ok": geometry["ok"],
                    "geometry_issue_codes": [
                        issue["code"] for issue in geometry["issues"]
                    ],
                    "keyword_coverage_ok": coverage["ok"],
                    "keyword_planned": f"{coverage['planned']}/{coverage['total']}",
                },
                "title_measurement": {
                    "font_size_px": measurement.font_size_px,
                    "line_height_px": measurement.line_height_px,
                    "lines": list(measurement.lines),
                    "widest_line_px": round(measurement.widest_line_px, 1),
                    "required_height_px": measurement.required_height_px,
                    "fits": measurement.fits,
                },
            }
        )
        if not geometry["ok"] or not coverage["ok"]:
            raise SystemExit(
                f"template {candidate} failed its own checks: "
                f"{[issue['code'] for issue in geometry['issues']]}"
            )

    return {
        "run_id": request.run_id,
        "execution_mode": ExecutionMode.real.value,
        "templates_available": [item["template_id"] for item in describe_templates()],
        "fonts": {
            family: [face.weight for face in default_font_registry().faces(family)]
            for family in default_font_registry().families()
        },
        "scenarios": scenarios,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(".scratch/layout-planner"))
    parser.add_argument("--template", choices=template_ids(), default=None)
    parser.add_argument("--title", default="静海之蓝")
    parser.add_argument("--subtitle", default="留一点宁静给自己")
    parser.add_argument("--no-subtitle", action="store_true")
    arguments = parser.parse_args()
    arguments.root.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(
        run_example(
            arguments.template,
            arguments.title,
            None if arguments.no_subtitle else arguments.subtitle,
        )
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    (arguments.root / "layout_planner_result.json").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()

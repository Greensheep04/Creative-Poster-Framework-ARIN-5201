import asyncio, importlib.util
from pathlib import Path
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def demo():
    path = ROOT / "examples/visual/workflow_demo.py"
    assert path.is_file(), "four-role integration demonstration required"
    spec = importlib.util.spec_from_file_location("visual_workflow_demo", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "scenario,ops,rounds",
    [
        ("initial", 1, 0),
        ("rerender", 1, 1),
        ("regenerate_asset", 2, 1),
        ("revise_layout", 1, 1),
        ("revise_layout_asset", 2, 1),
    ],
)
def test_four_role_real_png_workflow(tmp_path, scenario, ops, rounds):
    result, summary = asyncio.run(demo().run_demo(tmp_path, scenario=scenario))
    assert result.status.value == "passed"
    assert result.usage.image_operations == ops and result.revision_rounds == rounds
    assert summary["source_mode"] == "offline_fixture"
    assert summary["visual_quality_evaluated"] is False
    assert Image.open(summary["poster"]).size == (720, 1280)
    assert result.review.candidate_id == result.candidate.artifact_id


def test_zero_budget_returns_needs_review_without_candidate(tmp_path):
    from poster_framework import RunBudget

    result, summary = asyncio.run(
        demo().run_demo(tmp_path, budget=RunBudget(max_image_operations=0))
    )
    assert result.status.value == "needs_review"
    assert result.candidate is None and result.usage.image_operations == 0

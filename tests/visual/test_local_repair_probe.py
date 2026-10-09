import asyncio, importlib.util, json
from pathlib import Path
from poster_visual_producer.generators import FixtureImageGenerator
from .helpers import fonts, png


def test_repair_probe_checks_real_pixels_and_does_not_claim_review(tmp_path):
    p = Path(__file__).resolve().parents[2] / "examples/visual/local_acceptance.py"
    assert p.exists(), "real-backend repair acceptance runner required"
    from examples.visual.local_acceptance import run_repair_probe
    from poster_visual_producer.cases import PresetCase

    case = PresetCase.model_validate_json(
        (p.parent / "cases/ocean.json").read_text(encoding="utf-8")
    )
    count = [0]

    def source(req):
        count[0] += 1
        return png(color="blue" if count[0] % 2 else "green")

    for action in ("revise_layout", "regenerate_asset"):
        row = asyncio.run(
            run_repair_probe(
                case,
                FixtureImageGenerator(source),
                fonts(),
                tmp_path / action,
                action,
                seed=None,
            )
        )
        assert row["workflow_status"] == "needs_review"
        assert row["visual_quality_evaluated"] is False
        assert all(row["checks"].values())

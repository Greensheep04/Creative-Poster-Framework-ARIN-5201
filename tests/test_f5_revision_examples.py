import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("action", ["revise_brief", "revise_layout"])
def test_revision_example_runs_public_route_and_reads_versioned_memory(tmp_path, action):
    project_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            str(project_root / "examples" / "revision_actions.py"),
            "--root",
            str(tmp_path / action),
            "--action",
            action,
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(completed.stdout)

    assert result["revision_action"] == action
    assert result["execution_mode"] == "real"
    assert result["status"] == "passed"
    assert result["revision_rounds"] == 1
    assert result["review"]["candidate_id"] == result["candidate"]["artifact_id"]
    assert result["review"]["candidate_revision"] == result["candidate"]["revision"]
    assert result["review_outputs"][0]["decision"] == "fail"
    assert result["review_outputs"][0]["keyword_results"][0]["status"] == "fail"
    assert result["review_outputs"][1]["decision"] == "pass"
    assert all(
        item["status"] == "pass"
        for item in result["review_outputs"][1]["keyword_results"]
    )
    assert result["call_sequence"] == (
        ["creative_director", "layout_planner", "visual_producer", "quality_critic"] * 2
        if action == "revise_brief"
        else [
            "creative_director",
            "layout_planner",
            "visual_producer",
            "quality_critic",
            "layout_planner",
            "visual_producer",
            "quality_critic",
        ]
    )
    assert len(result["layout_note_events"]) == 2
    first_note, second_note = result["layout_note_events"]
    assert first_note["based_on"]
    assert first_note["read_note_id"] == first_note["note_id"]
    assert first_note["historical_note_content"] == first_note["content"]
    if action == "revise_layout":
        assert second_note["read_note_id"] == first_note["note_id"]
        assert second_note["historical_note_content"] == first_note["content"]
        assert second_note["prior_note_visible_in_context"] is True
    else:
        assert second_note["read_note_id"] != first_note["note_id"]
        assert second_note["based_on"] != first_note["based_on"]
        assert second_note["prior_note_visible_in_context"] is False
    if action == "revise_brief":
        assert result["initial_brief"] != result["final_brief"]
    else:
        assert result["initial_layout"] != result["final_layout"]

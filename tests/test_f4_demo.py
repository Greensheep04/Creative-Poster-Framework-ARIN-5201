import asyncio
import hashlib
import json
from importlib.util import find_spec
from pathlib import Path

import poster_framework as framework


def test_fake_model_demo_saves_all_four_scenarios_and_call_records(tmp_path):
    assert find_spec("poster_framework.demo") is not None
    from poster_framework.demo import run_demo

    report = asyncio.run(run_demo(tmp_path, delay_seconds=0))

    assert report["execution_mode"] == framework.ExecutionMode.fake.value
    assert report["delay_seconds"] == 0
    assert [item["initial_remaining_revisions"] for item in report["scenarios"]] == [
        0,
        1,
        2,
        3,
    ]
    expected = [
        (framework.WorkflowStatus.passed.value, 0, 4),
        (framework.WorkflowStatus.passed.value, 1, 6),
        (framework.WorkflowStatus.passed.value, 2, 8),
        (framework.WorkflowStatus.needs_review.value, 2, 8),
    ]
    assert len({item["run_id"] for item in report["scenarios"]}) == 4

    for item, (status, rounds, call_count) in zip(report["scenarios"], expected, strict=True):
        assert item["status"] == status
        assert item["revision_rounds"] == rounds
        assert item["model_calls"] == call_count
        assert item["record_path"]
        assert item["call_log_path"]

        record_path = Path(item["record_path"])
        call_log_path = Path(item["call_log_path"])
        record = framework.RunRecord.model_validate_json(
            record_path.read_text(encoding="utf-8")
        )
        session = framework.RunSession.load(record_path, root=tmp_path)
        candidate = framework.ArtifactReference.model_validate(item["candidate"])
        candidate_data = session.file_store.read(candidate)
        calls = json.loads(call_log_path.read_text(encoding="utf-8"))
        assert record.final_result["status"] == status
        assert candidate.uri.endswith("candidate.json")
        assert hashlib.sha256(candidate_data).hexdigest() == candidate.sha256
        assert json.loads(candidate_data)["remaining_revisions"] >= 0
        assert len(calls) == call_count
        assert all("response" in call for call in calls)
        assert [call["role"] for call in calls] == item["call_sequence"]

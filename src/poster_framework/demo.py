from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter

from .fake import FakePosterModel
from .types import ExecutionMode, RunBudget
from .workflow import PosterWorkflow, WorkflowStatus


async def run_demo(
    root: str | Path,
    *,
    delay_seconds: float = 15,
) -> dict[str, object]:
    root_path = Path(root).resolve()
    scenario_results = []
    started = perf_counter()
    for initial_remaining_revisions in range(4):
        model = FakePosterModel(
            initial_remaining_revisions=initial_remaining_revisions,
            delay_seconds=delay_seconds,
        )
        workflow = PosterWorkflow(
            model.agent_set(),
            root=root_path,
            budget=RunBudget(max_revision_rounds=2),
        )
        scenario_started = perf_counter()
        result = await workflow.run(
            ["tea", "caffeine-free"],
            constraints={"tone": "calm"},
            execution_mode=ExecutionMode.fake,
        )
        elapsed_seconds = perf_counter() - scenario_started
        expected_status = (
            WorkflowStatus.needs_review
            if initial_remaining_revisions == 3
            else WorkflowStatus.passed
        )
        expected_rounds = min(initial_remaining_revisions, 2)
        expected_calls = 4 + 2 * expected_rounds
        if (
            result.status != expected_status
            or result.revision_rounds != expected_rounds
            or result.usage.model_calls != expected_calls
        ):
            raise AssertionError(
                f"FakeModel scenario {initial_remaining_revisions} did not match its expected result"
            )
        if result.candidate is None:
            raise AssertionError("FakeModel scenario did not publish a candidate")
        call_log_path = result.record_path.parent / "fake-model-calls.json"
        call_log_path.write_text(
            json.dumps(
                model.calls,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        scenario_results.append(
            {
                "initial_remaining_revisions": initial_remaining_revisions,
                "run_id": result.original_request.run_id,
                "execution_mode": ExecutionMode.fake.value,
                "status": result.status.value,
                "revision_rounds": result.revision_rounds,
                "model_calls": result.usage.model_calls,
                "call_sequence": [role.value for role in result.call_sequence],
                "candidate": result.candidate.model_dump(mode="json"),
                "review": result.review.model_dump(mode="json") if result.review else None,
                "reason": result.reason,
                "record_path": str(result.record_path),
                "call_log_path": str(call_log_path),
                "elapsed_seconds": round(elapsed_seconds, 3),
            }
        )
    return {
        "execution_mode": ExecutionMode.fake.value,
        "delay_seconds": float(delay_seconds),
        "total_elapsed_seconds": round(perf_counter() - started, 3),
        "scenarios": scenario_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m poster_framework.demo")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(".scratch/framework/f4-demo"),
    )
    parser.add_argument("--delay-seconds", type=float, default=15)
    arguments = parser.parse_args()
    report = asyncio.run(
        run_demo(arguments.root, delay_seconds=arguments.delay_seconds)
    )
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()

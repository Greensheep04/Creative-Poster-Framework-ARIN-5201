from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

import poster_framework
from poster_framework import (
    ExecutionMode,
    PosterWorkflow,
    run_single_agent_from_request,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "scripts" / "package-manifest.txt"
FORBIDDEN_PARTS = {".git", ".scratch", ".venv", "__pycache__", "runs"}
FORBIDDEN_SUFFIXES = {".pdf", ".xlsx", ".xls", ".env"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest_names() -> tuple[str, ...]:
    names = tuple(
        line.strip()
        for line in MANIFEST_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    if len(names) != len(set(names)):
        raise ValueError("package manifest contains duplicate paths")
    for name in names:
        relative_path = PurePosixPath(name)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"package manifest path must stay inside the project: {name}")
        if FORBIDDEN_PARTS.intersection(relative_path.parts):
            raise ValueError(f"package manifest contains a forbidden directory: {name}")
        if relative_path.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise ValueError(f"package manifest contains a forbidden file type: {name}")
    return tuple(sorted(names))


def verify_archive(archive_path: Path, expected_names: tuple[str, ...]) -> dict[str, object]:
    with ZipFile(archive_path) as archive:
        actual_names = tuple(sorted(name for name in archive.namelist() if not name.endswith("/")))
        if actual_names != expected_names:
            missing = sorted(set(expected_names) - set(actual_names))
            extra = sorted(set(actual_names) - set(expected_names))
            raise AssertionError(f"archive membership mismatch: missing={missing}, extra={extra}")
        member_hashes = {}
        for name in actual_names:
            relative_path = PurePosixPath(name)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise AssertionError(f"archive contains an unsafe path: {name}")
            if FORBIDDEN_PARTS.intersection(relative_path.parts):
                raise AssertionError(f"archive contains a forbidden directory: {name}")
            if relative_path.suffix.lower() in FORBIDDEN_SUFFIXES:
                raise AssertionError(f"archive contains a forbidden file type: {name}")
            member_hash = hashlib.sha256(archive.read(name)).hexdigest()
            source_hash = sha256(ROOT / Path(name))
            if member_hash != source_hash:
                raise AssertionError(f"archive member differs from the current package file: {name}")
            member_hashes[name] = {
                "archive_sha256": member_hash,
                "source_sha256": source_hash,
                "matches": True,
            }
    return {
        "path": str(archive_path),
        "sha256": sha256(archive_path),
        "member_count": len(actual_names),
        "members": list(actual_names),
        "member_hashes": member_hashes,
    }


def execute(
    name: str,
    command: list[str],
    *,
    root: Path,
    evidence_dir: Path,
    checks: list[dict[str, object]],
    environment: dict[str, str],
) -> Path:
    log_path = evidence_dir / f"{name}.log"
    completed = subprocess.run(
        command,
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        env=environment,
    )
    log_path.write_text(completed.stdout, encoding="utf-8")
    checks.append(
        {
            "name": name,
            "command": command,
            "exit_code": completed.returncode,
            "log_path": str(log_path.relative_to(root)),
            "log_sha256": sha256(log_path),
        }
    )
    if completed.returncode != 0:
        write_report(evidence_dir, {"status": "failed", "checks": checks})
        raise SystemExit(completed.returncode)
    return log_path


def read_json_log(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def run_ids_for(name: str, payload: object) -> list[str]:
    if name == "four-role-demo":
        if not isinstance(payload, dict):
            raise TypeError("four-role demo output must be a JSON object")
        scenarios = payload["scenarios"]
        if not isinstance(scenarios, list) or len(scenarios) != 4:
            raise AssertionError("four-role demo must report four budget scenarios")
        expected_statuses = ["passed", "passed", "passed", "needs_review"]
        if [item["status"] for item in scenarios] != expected_statuses:
            raise AssertionError("four-role demo returned unexpected review states")
        if payload["execution_mode"] != ExecutionMode.fake.value:
            raise AssertionError("four-role demo must explicitly use FakePosterModel")
        if payload["delay_seconds"] != 15.0:
            raise AssertionError("four-role demo must retain the documented default wait")
        return [item["run_id"] for item in scenarios]
    if not isinstance(payload, dict):
        raise TypeError(f"{name} output must be a JSON object")
    if name == "single-role":
        if payload["execution_mode"] != ExecutionMode.real.value:
            raise AssertionError("single-role example must use the default real mode")
        return [payload["run_id"]]
    if name == "format-upgrade":
        if payload["source_format"]["version"] != "1":
            raise AssertionError("format example source must use v1")
        if payload["conversion"]["target_format"]["version"] != "2":
            raise AssertionError("format example must convert to v2")
        return [payload["run_id"]]
    if name == "tool-catalog":
        if payload["execution_mode"] != ExecutionMode.fake.value:
            raise AssertionError("tool example must identify its FakeModel mode")
        if payload["tool_calls"] != 1 or payload["status"] != "passed":
            raise AssertionError("tool example did not execute and record one tool call")
        return [payload["run_id"]]
    return []


def write_report(evidence_dir: Path, report: dict[str, object]) -> None:
    report_path = evidence_dir / "verification.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--archive",
        type=Path,
        default=Path("creative-poster-framework.zip"),
    )
    parser.add_argument(
        "--record-dir",
        type=Path,
        default=Path(".scratch/package-verification"),
    )
    arguments = parser.parse_args()
    archive_path = arguments.archive
    if not archive_path.is_absolute():
        archive_path = ROOT / archive_path
    archive_path = archive_path.resolve()
    evidence_dir = arguments.record_dir
    if not evidence_dir.is_absolute():
        evidence_dir = ROOT / evidence_dir
    evidence_dir.mkdir(parents=True, exist_ok=True)
    temporary_dir = evidence_dir / "tmp"
    temporary_dir.mkdir(parents=True, exist_ok=True)
    pytest_base_dir = evidence_dir / "pytest-tmp"
    pytest_base_dir.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["TMPDIR"] = str(temporary_dir)

    expected_names = manifest_names()
    archive_info = verify_archive(archive_path, expected_names)
    import_path = Path(poster_framework.__file__).resolve()
    if not import_path.is_relative_to(ROOT / "src"):
        raise AssertionError(f"poster_framework imported outside this package: {import_path}")
    workflow_default = inspect.signature(PosterWorkflow.run).parameters[
        "execution_mode"
    ].default
    single_default = inspect.signature(run_single_agent_from_request).parameters[
        "execution_mode"
    ].default
    if workflow_default != ExecutionMode.real or single_default != ExecutionMode.real:
        raise AssertionError("public entry points must default to ExecutionMode.real")

    checks: list[dict[str, object]] = []
    commands = (
        (
            "pytest",
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--basetemp",
                str(pytest_base_dir),
            ],
        ),
        (
            "four-role-demo",
            [
                sys.executable,
                "examples/four_role_walkthrough.py",
                "--root",
                str(evidence_dir / "four-role-demo"),
            ],
        ),
        (
            "single-role",
            [
                sys.executable,
                "examples/single_role.py",
                "--root",
                str(evidence_dir / "single-role"),
            ],
        ),
        (
            "format-upgrade",
            [
                sys.executable,
                "examples/format_upgrade.py",
                "--root",
                str(evidence_dir / "format-upgrade"),
            ],
        ),
        (
            "tool-catalog",
            [
                sys.executable,
                "examples/tool_catalog.py",
                "--root",
                str(evidence_dir / "tool-catalog"),
            ],
        ),
    )
    run_ids: dict[str, list[str]] = {}
    for name, command in commands:
        log_path = execute(
            name,
            command,
            root=ROOT,
            evidence_dir=evidence_dir,
            checks=checks,
            environment=environment,
        )
        if name != "pytest":
            run_ids[name] = run_ids_for(name, read_json_log(log_path))
            checks[-1]["run_ids"] = run_ids[name]

    report = {
        "status": "passed",
        "archive": archive_info,
        "source_hashes": {
            name: sha256(ROOT / Path(name))
            for name in expected_names
        },
        "import_origin": str(import_path),
        "default_execution_mode": ExecutionMode.real.value,
        "checks": checks,
        "run_ids": run_ids,
        "evidence_dir": str(evidence_dir),
        "temporary_dir": str(temporary_dir),
    }
    write_report(evidence_dir, report)
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()

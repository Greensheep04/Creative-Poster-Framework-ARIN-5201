import json
import subprocess
import sys
from pathlib import Path


def test_single_role_output_matches_packaged_reference(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            str(project_root / "examples" / "single_role.py"),
            "--root",
            str(tmp_path / "single-role"),
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(completed.stdout)
    reference = json.loads(
        (project_root / "examples" / "sample-single-role-output.json").read_text(
            encoding="utf-8"
        )
    )

    assert result["execution_mode"] == "real"
    assert result["output"] == reference

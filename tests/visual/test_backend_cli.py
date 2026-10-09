import subprocess, sys, json
from pathlib import Path


def test_single_role_cli_accepts_backend_config():
    result = subprocess.run(
        [sys.executable, "-m", "poster_visual_producer", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--backend-config" in result.stdout, (
        "same backend config required for standalone A3"
    )


def test_missing_model_environment_variable_rejected():
    import os
    from poster_visual_producer.backends import create_generator
    import pytest

    os.environ.pop("POSTER_TEST_MISSING_MODEL", None)
    with pytest.raises(ValueError, match="environment"):
        create_generator(
            {
                "kind": "sdxl",
                "model_dir": "${POSTER_TEST_MISSING_MODEL}",
                "revision": "a" * 40,
            }
        )

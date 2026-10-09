import subprocess, sys
from pathlib import Path


def test_setup_rejects_corrupt_cached_wheel_before_remote_install(tmp_path):
    root = Path(__file__).resolve().parents[2]
    cache = tmp_path / "cache"
    wheels = cache / "local-wheels"
    wheels.mkdir(parents=True)
    (wheels / "torch-2.8.0+cu128-cp311-cp311-win_amd64.whl").write_bytes(
        b"corrupt-wheel"
    )

    def quote(p):
        return "'" + str(p).replace("'", "''") + "'"

    command = (
        "function uv { throw 'remote install attempted before cache validation' }; & "
        + quote(root / "scripts/setup-local-gpu.ps1")
        + " -EnvironmentPrefix "
        + quote(Path(sys.executable).parent)
        + " -UvCache "
        + quote(cache)
        + " -SkipModelDownload"
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode != 0
    assert "wheel checksum" in (result.stdout + result.stderr).lower()

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


def test_production_cli_rejects_inline_secret_without_echo(tmp_path):
    root=Path(__file__).resolve().parents[2]
    secret='sentinel-inline-key-never-echo-92817'
    config=tmp_path/'backend.json'
    config.write_text(json.dumps({'kind':'cloud_api','provider':'test','model':'test','endpoint':'https://example.org/v1/images/generations','api_key_env':'POSTER_TEST_KEY','api_key':secret}),encoding='utf-8')
    result=subprocess.run([sys.executable,'-m','poster_visual_producer','--backend-config',str(config),'--fonts',str(root/'examples/visual/fonts.json'),'--font-base',str(root),'--upstream-record',str(tmp_path/'missing-upstream.json'),'--root',str(tmp_path),'--output-record',str(tmp_path/'output.json')],capture_output=True,text=True,timeout=30)
    assert result.returncode!=0
    assert 'Extra inputs are not permitted' in result.stderr
    assert secret not in result.stderr+result.stdout

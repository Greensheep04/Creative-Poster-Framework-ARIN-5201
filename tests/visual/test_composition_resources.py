import asyncio
import threading
from types import SimpleNamespace

import pytest


def test_local_backend_can_release_its_pipeline_without_loading_or_gpu_calls():
    from poster_visual_producer.backends import LocalSDXLGenerator, SDXLConfig
    generator = LocalSDXLGenerator(SDXLConfig(model_dir="unused", revision="a" * 40))
    calls = []
    generator._pipeline = object()
    generator._torch = SimpleNamespace(cuda=SimpleNamespace(empty_cache=lambda: calls.append("released")))
    generator.close()
    assert generator._pipeline is None
    assert generator._torch is None
    assert calls == ["released"]
    generator.close()
    assert calls == ["released"]


@pytest.mark.parametrize("fails", [False, True])
def test_owned_generator_is_closed_after_workflow_even_on_failure(tmp_path, monkeypatch, fails):
    import examples.team_workflow as runner
    from creative.creative_director import OfflineCreativeDirectorAgent
    original = runner.FixtureImageGenerator
    closed = []
    def factory(*args, **kwargs):
        generator = original(*args, **kwargs)
        generator.close = lambda: closed.append(True)
        return generator
    monkeypatch.setattr(runner, "FixtureImageGenerator", factory)
    if fails:
        async def fail(*args, **kwargs):
            raise RuntimeError("controlled agent failure")
        monkeypatch.setattr(OfflineCreativeDirectorAgent, "run", fail)
        with pytest.raises(RuntimeError, match="controlled"):
            asyncio.run(runner.run_team(tmp_path, ["ocean", "plastic", "hope"], offline=True))
    else:
        asyncio.run(runner.run_team(tmp_path, ["ocean", "plastic", "hope"], offline=True))
    assert closed == [True]

import asyncio, importlib.util, io, json
from types import SimpleNamespace
import pytest
from PIL import Image
from poster_visual_producer.generators import compile_request


def module():
    assert importlib.util.find_spec("poster_visual_producer.backends"), (
        "local GPU backend required"
    )
    from poster_visual_producer import backends

    return backends


def config(tmp_path, **kw):
    values = dict(model_dir=tmp_path, revision="a" * 40, steps=20, guidance_scale=6.0)
    values.update(kw)
    return module().SDXLConfig(**values)


def test_invalid_sampling_rejected(tmp_path):
    with pytest.raises(ValueError):
        config(tmp_path, steps=0)
    with pytest.raises(ValueError):
        config(tmp_path, guidance_scale=float("nan"))


def test_local_backend_lazy_and_explicit_capabilities(tmp_path):
    g = module().LocalSDXLGenerator(config(tmp_path))
    assert g._pipeline is None
    assert g.capabilities.supports_seed and g.capabilities.supports_negative_prompt
    assert not g.capabilities.offline_fixture
    assert dict(g.capabilities.generation_parameters)["steps"] == 20
    assert g.capabilities.weights_version == "a" * 40


def test_missing_weights_fail_without_download(tmp_path, spec_data):
    g = module().LocalSDXLGenerator(config(tmp_path))
    with pytest.raises(ValueError, match="model_index"):
        asyncio.run(
            g.generate(
                compile_request(spec_data["asset_plan"][0], g.capabilities, seed=7)
            )
        )


def test_sdk_boundary_seed_negative_prompt_and_png(tmp_path, spec_data):
    g = module().LocalSDXLGenerator(config(tmp_path))
    seen = {}

    class Pipe:
        def __call__(self, **kw):
            seen.update(kw)
            return SimpleNamespace(
                images=[Image.new("RGB", (kw["width"], kw["height"]), "green")]
            )

    class Seed:
        def manual_seed(self, seed):
            self.seed = seed
            return self

    class Scope:
        def __enter__(self):
            pass

        def __exit__(self, *args):
            pass

    g._pipeline = Pipe()
    g._torch = SimpleNamespace(
        Generator=lambda device: Seed(), inference_mode=Scope, __version__="test"
    )
    request = compile_request(spec_data["asset_plan"][0], g.capabilities, seed=7)
    result = asyncio.run(g.generate(request))
    assert seen["generator"].seed == 7
    assert seen["negative_prompt"] == request.negative_prompt
    assert seen["num_inference_steps"] == 20
    assert Image.open(io.BytesIO(result.data)).size == request.size
    assert result.provenance["source_mode"] == "local_gpu"
    assert result.provenance["actual_seed"] == 7
    assert result.usage["api_cost_usd"] == 0


def test_external_factory_remains_replaceable(tmp_path, monkeypatch):
    import sys
    from poster_visual_producer.generators import FixtureImageGenerator

    fake = FixtureImageGenerator(lambda req: b"")
    monkeypatch.setitem(
        sys.modules, "trusted_test_backend", SimpleNamespace(build=lambda opts: fake)
    )
    result = module().create_generator(
        {
            "kind": "factory",
            "factory": "trusted_test_backend:build",
            "options": {"a": 1},
        }
    )
    assert result is fake


def test_backend_sampling_parameters_change_cache_key(tmp_path, spec_data):
    from poster_framework.generation import GenerationFingerprint

    m = module()
    a = m.LocalSDXLGenerator(config(tmp_path, steps=20))
    b = m.LocalSDXLGenerator(config(tmp_path, steps=30))
    fingerprints = []
    for g in (a, b):
        req = compile_request(spec_data["asset_plan"][0], g.capabilities, seed=7)
        fingerprints.append(
            GenerationFingerprint.build(
                provider=g.capabilities.provider,
                model=g.capabilities.model,
                weights=g.capabilities.weights_version,
                prompt=req.prompt,
                parameters={"backend": dict(g.capabilities.generation_parameters)},
                reference_materials=(),
                processing_config={},
            ).key
        )
    assert fingerprints[0] != fingerprints[1]


def test_guidance_without_cfg_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        config(tmp_path, guidance_scale=1.0)


def test_model_manifest_requires_all_pipeline_components(tmp_path):
    import hashlib

    m = module()
    c = config(tmp_path)
    files = {}
    for name in (
        "model_index.json",
        "unet/diffusion_pytorch_model.fp16.safetensors",
        "vae/diffusion_pytorch_model.fp16.safetensors",
        "text_encoder/model.fp16.safetensors",
        "text_encoder_2/model.fp16.safetensors",
    ):
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"fake")
        files[name] = hashlib.sha256(b"fake").hexdigest()
    (tmp_path / "poster-model-manifest.json").write_text(
        json.dumps({"model_id": c.model_id, "revision": c.revision, "files": files}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Incomplete"):
        m.verify_weights(c)


def test_actual_agent_invalidates_cache_when_sampling_settings_change(
    tmp_path, spec_data, brief_data
):
    import copy
    from dataclasses import replace
    from poster_framework import AgentRole
    from .test_agent import setup, run

    a, session, spec, brief, producer = setup(tmp_path, spec_data, brief_data)
    first = run(a, producer, session, spec, "initial-settings")
    generator = a["FixtureImageGenerator"](lambda req: a["png"]("green"))
    generator.capabilities = replace(
        generator.capabilities, generation_parameters=(("steps", 30),)
    )
    producer.generator = generator
    spec = copy.deepcopy(spec)
    spec["spec_version"] = 2
    spec["elements"][1]["box"]["y"] = 84
    a["publish"](
        session, "layout", spec, AgentRole.layout_planner, "updated-settings-layout"
    )
    second = run(a, producer, session, spec, "updated-settings")
    assert session.usage.image_operations == 2
    assert (
        first.content["assets"][0]["source_ref"]["sha256"]
        != second.content["assets"][0]["source_ref"]["sha256"]
    )

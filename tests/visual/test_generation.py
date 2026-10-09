import asyncio, importlib.util, io
import pytest
from PIL import Image

def api():
    assert importlib.util.find_spec("poster_visual_producer.generators"), "generator adapter is required"
    from poster_visual_producer import generators
    return generators

def data():
    b=io.BytesIO(); Image.new("RGB",(100,100),"blue").save(b,"PNG"); return b.getvalue()

def test_capabilities_compile_actual_supported_size(spec_data):
    g=api()
    caps=g.GeneratorCapabilities("sdk","model-v1",((1024,1024),(1024,1536)))
    req=g.compile_request(spec_data["asset_plan"][0],caps)
    assert req.size==(1024,1536)
    assert req.negative_prompt is None
    assert "暴风雨" in req.prompt and req.adaptations
    assert req.seed_reason=="unsupported"

def test_supported_parameters_preserved(spec_data):
    g=api(); caps=g.GeneratorCapabilities("sdk","model-v1",((1024,1024),),supports_seed=True,supports_negative_prompt=True)
    req=g.compile_request(spec_data["asset_plan"][0],caps,seed=7)
    assert req.seed==7 and req.seed_reason=="specified"
    assert req.negative_prompt==spec_data["asset_plan"][0]["negative_prompt"]

def test_unsupported_seed_rejected(spec_data):
    g=api(); caps=g.GeneratorCapabilities("sdk","v1",((1024,1024),))
    with pytest.raises(ValueError): g.compile_request(spec_data["asset_plan"][0],caps,seed=7)

def test_negative_prompt_can_require_explicit_rejection(spec_data):
    g=api(); caps=g.GeneratorCapabilities("sdk","v1",((1024,1024),))
    with pytest.raises(ValueError): g.compile_request(spec_data["asset_plan"][0],caps,negative_policy="error")

def test_callback_result_validated(spec_data):
    g=api(); caps=g.GeneratorCapabilities("sdk","v1",((1024,1024),))
    async def callback(req): return g.GeneratedImage(b"corrupt",request_id="r1")
    adapter=g.CallableImageGenerator(caps,callback)
    with pytest.raises(ValueError): asyncio.run(adapter.generate(g.compile_request(spec_data["asset_plan"][0],caps)))

def test_fixture_is_explicit_and_decodable(spec_data):
    g=api(); adapter=g.FixtureImageGenerator(lambda req:data(),sizes=((1024,1024),))
    result=asyncio.run(adapter.generate(g.compile_request(spec_data["asset_plan"][0],adapter.capabilities)))
    assert adapter.capabilities.offline_fixture is True
    assert result.provenance["source_mode"]=="offline_fixture"
    assert result.usage is None and Image.open(io.BytesIO(result.data)).size==(100,100)

import asyncio
import base64
import io
import json
import os
from copy import deepcopy
import httpx
import pytest
from PIL import Image

SECRET="sk-local-only-sentinel-123456789"

def test_a1_inline_key_and_endpoint_override_do_not_touch_environment(monkeypatch):
    import creative.creative_director as cd
    monkeypatch.setenv("HF_TOKEN","ambient-key-must-not-be-used")
    original=httpx.AsyncClient; calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json={"choices":[{"finish_reason":"stop","message":{"content":"ok"}}]})
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
    text=asyncio.run(cd.call_hugging_face("huggingface","test-model",{}, {},api_key=SECRET,endpoint="https://example.com/v1/chat/completions",timeout_seconds=8))
    assert text=="ok" and calls[0].headers["authorization"]=="Bearer "+SECRET
    assert str(calls[0].url)=="https://example.com/v1/chat/completions"
    assert os.environ["HF_TOKEN"]=="ambient-key-must-not-be-used"
    assert SECRET not in calls[0].content.decode()

def test_a1_provider_error_redacts_inline_key(monkeypatch):
    import creative.creative_director as cd
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw:original(transport=httpx.MockTransport(lambda r:httpx.Response(403,json={"error":SECRET})),**kw))
    with pytest.raises(RuntimeError) as err:
        asyncio.run(cd.call_hugging_face("hf","model",{}, {},api_key=SECRET))
    assert SECRET not in str(err.value)

def test_cloud_inline_key_is_private_and_environment_fallback_survives(monkeypatch):
    from poster_visual_producer.cloud_backend import CloudAPIConfig,CloudImageGenerator
    config=CloudAPIConfig(provider="test",model="test",endpoint="https://example.com/v1/images/generations",api_key_env="LEGACY_IMAGE_KEY",sizes=((1024,1024),))
    generator=CloudImageGenerator(config,api_key=SECRET)
    assert SECRET not in repr(generator) and SECRET not in config.model_dump_json()
    assert generator._api_key.get_secret_value()==SECRET
    monkeypatch.setenv("LEGACY_IMAGE_KEY","legacy-test-key")
    legacy=CloudImageGenerator(config)
    assert legacy._api_key is None and os.environ["LEGACY_IMAGE_KEY"]=="legacy-test-key"


def test_central_a1_options_reach_actual_agent_without_logging_key(tmp_path,monkeypatch):
    from pathlib import Path
    import creative.creative_director as cd
    from poster_framework import OriginalRequest,run_single_agent_from_request
    from poster_visual_producer.team_creative import creative_registration
    raw=json.loads((Path(cd.__file__).parent/"resources/sample-output.json").read_text(encoding="utf-8"))["content"]
    calls=[];original=httpx.AsyncClient
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json={"choices":[{"finish_reason":"stop","message":{"content":json.dumps(raw)}}]})
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
    registration=creative_registration(model="configured-model",options={"api_key":SECRET,"endpoint":"https://example.com/custom/chat/completions","max_tokens":333,"temperature":0.2,"timeout_seconds":8})
    assert SECRET not in repr(registration.config)
    request=OriginalRequest.from_keywords("config-a1",raw["keywords"])
    asyncio.run(run_single_agent_from_request(registration,request,root=tmp_path))
    body=json.loads(calls[0].content)
    assert body["max_tokens"]==333 and body["temperature"]==0.2
    assert calls[0].headers["authorization"]=="Bearer "+SECRET
    for path in tmp_path.rglob("*.json"):assert SECRET not in path.read_text(encoding="utf-8")

def test_a4_accepts_private_key_without_environment(monkeypatch):
    from poster_critic.providers.chat import ChatProvider,ProviderConfig
    monkeypatch.setenv("A4_API_KEY","ambient-key-must-not-be-used")
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json={"choices":[{"finish_reason":"stop","message":{"content":"{}"}}]})
    provider=ChatProvider(ProviderConfig(profile="compatible",base_url="https://example.com/v1",model="vision",response_format="prompt_json"),api_key=SECRET,transport=httpx.MockTransport(handle))
    asyncio.run(provider._invoke_body({"model":"vision"},"https://example.com/v1/chat/completions"))
    assert calls[0].headers["authorization"]=="Bearer "+SECRET
    assert SECRET not in repr(provider) and os.environ["A4_API_KEY"]=="ambient-key-must-not-be-used"

def test_cloud_factory_uses_inline_key_in_actual_http_request(tmp_path,monkeypatch):
    from pathlib import Path
    from tests.visual.test_cloud_backend import server
    from tests.visual.helpers import make_session,call_context,fonts
    from poster_visual_producer.backends import create_generator
    from poster_visual_producer.agent import VisualProducerAgent
    root=Path(__file__).resolve().parents[1]
    spec=json.loads((root/"examples/visual/spec.json").read_text(encoding="utf-8"))
    brief=json.loads((root/"examples/visual/brief.json").read_text(encoding="utf-8"))
    monkeypatch.setenv("INLINE_TEST_KEY","ambient-key-must-not-be-used")
    with server() as (endpoint,calls):
        generator=create_generator({"kind":"cloud_api","provider":"test","model":"test","endpoint":endpoint,"api_key_env":"INLINE_TEST_KEY","sizes":[[1024,1536]]},api_key=SECRET)
        session,spec,_=make_session(tmp_path,spec,brief);inputs,context=call_context(session,spec,"inline-cloud")
        output=asyncio.run(VisualProducerAgent(generator,fonts()).run(inputs,context))
    assert calls[0][1]["Authorization"]=="Bearer "+SECRET
    assert SECRET not in json.dumps(output.content)
    assert os.environ["INLINE_TEST_KEY"]=="ambient-key-must-not-be-used"


def test_cloud_rejects_inline_header_controls_without_echo():
    from poster_visual_producer.cloud_backend import CloudAPIConfig,CloudImageGenerator
    config=CloudAPIConfig(provider="test",model="test",endpoint="https://example.com/v1/images/generations",api_key_env="KEY")
    with pytest.raises(ValueError) as err:CloudImageGenerator(config,api_key=SECRET+"\n")
    assert SECRET not in str(err.value)

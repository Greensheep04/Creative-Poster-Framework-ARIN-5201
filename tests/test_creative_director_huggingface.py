import asyncio
import importlib
from importlib.resources import files
import json
from pathlib import Path

import httpx
import pytest


TEST_TOKEN = "hf_test_token_not_a_real_credential"
DEFAULT_MODEL = "Qwen/Qwen3-4B-Instruct-2507"


@pytest.fixture
def director(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HF_MODEL", raising=False)
    return importlib.import_module("creative.creative_director")


@pytest.fixture
def brief():
    reference = files("creative") / "resources" / "sample-output.json"
    return json.loads(reference.read_text(encoding="utf-8"))["content"]


@pytest.fixture
def mock_api(monkeypatch, director):
    """Every HTTP request stays inside a MockTransport, even in failure tests."""
    original_client = httpx.AsyncClient
    requests = []
    responses = []

    def handler(request):
        requests.append(request)
        if not responses:
            pytest.fail("Unexpected Hugging Face request")
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def client(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    return requests, responses


def completion(text, *, finish_reason="stop"):
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": text},
            }
        ]
    }


def call_api(director):
    return asyncio.run(
        director.call_hugging_face(
            "huggingface",
            DEFAULT_MODEL,
            {"messages": [{"role": "user", "content": "Create a poster brief."}]},
            {"max_tokens": 2048},
        )
    )


def test_live_agent_wraps_model_brief_and_records_one_call_without_token(
    director, brief, mock_api, monkeypatch, tmp_path
):
    requests, responses = mock_api
    monkeypatch.setenv("HF_TOKEN", TEST_TOKEN)
    responses.append(httpx.Response(200, json=completion(json.dumps(brief))))

    result = asyncio.run(director.run_creative_director(tmp_path))

    assert result["output"]["content"] == brief
    assert result["output"]["content_format"] == {
        "name": "poster.creative-brief", "version": "2"
    }
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://router.huggingface.co/featherless-ai/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {TEST_TOKEN}"
    body = json.loads(request.content)
    assert body["model"] == DEFAULT_MODEL
    assert "response_format" not in body
    assert body["max_tokens"] == 2048
    assert body["temperature"] == 0.7
    assert body["messages"][0]["role"] == "system"
    user_text = "\n".join(
        message["content"] for message in body["messages"] if message["role"] == "user"
    )
    for text in ("green tea", "caffeine-free", "calm", "forest green"):
        assert text in user_text
    assert TEST_TOKEN not in request.content.decode()

    record = json.loads(Path(result["record_path"]).read_text(encoding="utf-8"))
    assert record["usage"]["model_calls"] == 1
    assert len(record["model_events"]) == 1
    event = record["model_events"][0]
    assert event["provider"] == "huggingface"
    assert event["model"] == DEFAULT_MODEL
    assert event["status"] == "succeeded"
    assert event["executed"] is True
    assert "response_format" not in event["parameters"]
    for path in tmp_path.rglob("*.json"):
        assert TEST_TOKEN not in path.read_text(encoding="utf-8")


def test_missing_token_fails_before_network(director, mock_api):
    with pytest.raises(RuntimeError, match="HF_TOKEN"):
        call_api(director)

    assert mock_api[0] == []


@pytest.mark.parametrize("status", [400, 401, 403, 402, 429, 500])
def test_http_failures_report_status_without_token_or_retry(
    director, mock_api, monkeypatch, status
):
    monkeypatch.setenv("HF_TOKEN", TEST_TOKEN)
    requests, responses = mock_api
    responses.append(httpx.Response(status, json={"error": TEST_TOKEN}))

    with pytest.raises(RuntimeError, match=str(status)) as error:
        call_api(director)

    assert TEST_TOKEN not in str(error.value)
    assert len(requests) == 1


@pytest.mark.parametrize("error_format", ["string", "nested", "plaintext"])
def test_provider_error_reports_actual_detail_and_requested_model(
    director, mock_api, monkeypatch, error_format
):
    monkeypatch.setenv("HF_TOKEN", TEST_TOKEN)
    detail = "Requested model is unavailable on this route."
    if error_format == "string":
        response = httpx.Response(400, json={"error": detail})
    elif error_format == "nested":
        response = httpx.Response(400, json={"error": {"message": detail}})
    else:
        response = httpx.Response(400, text=detail)
    requests, responses = mock_api
    responses.append(response)

    with pytest.raises(RuntimeError) as error:
        call_api(director)

    message = str(error.value)
    assert "400" in message
    assert DEFAULT_MODEL in message
    assert detail in message
    assert len(requests) == 1


def test_provider_error_redacts_credentials_before_truncating(director):
    token = "supplied-test-secret-value"
    response = httpx.Response(
        400,
        json={"error": (
            f"Model unavailable.\n  supplied={token}; other=hf_otherSecret123456 "
            "Authorization: Bearer service-key_123.ab-c"
        )},
    )

    detail = director.provider_error_detail(response, token)

    assert "Model unavailable." in detail
    assert "\n" not in detail
    for secret in (token, "hf_otherSecret123456", "service-key_123.ab-c"):
        assert secret not in detail

    long_token = "hf_sensitive" + "A" * 80
    response = httpx.Response(400, text="x" * 790 + " " + long_token)
    detail = director.provider_error_detail(response, long_token)

    assert len(detail) <= 800
    assert "hf_" not in detail


def test_timeout_reports_failure_without_retry_or_secret(director, mock_api, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", TEST_TOKEN)
    requests, responses = mock_api
    responses.append(httpx.ReadTimeout(f"request timed out with {TEST_TOKEN}"))

    with pytest.raises(RuntimeError, match="(?i)timed out|timeout") as error:
        call_api(director)

    assert TEST_TOKEN not in str(error.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {}}]},
        completion(None),
        completion(" \n"),
        completion('{"keywords":', finish_reason="length"),
    ],
)
def test_malformed_empty_or_truncated_responses_fail(director, mock_api, monkeypatch, payload):
    monkeypatch.setenv("HF_TOKEN", TEST_TOKEN)
    mock_api[1].append(httpx.Response(200, json=payload))

    with pytest.raises(ValueError):
        call_api(director)


def test_non_json_http_response_fails(director, mock_api, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", TEST_TOKEN)
    mock_api[1].append(httpx.Response(200, text="upstream did not return JSON"))

    with pytest.raises(ValueError):
        call_api(director)


@pytest.mark.parametrize("invalid", ["json", "schema", "keywords"])
def test_model_content_is_validated_before_output_is_published(
    director, brief, mock_api, monkeypatch, tmp_path, invalid
):
    monkeypatch.setenv("HF_TOKEN", TEST_TOKEN)
    if invalid == "schema":
        del brief["copy"]["headline"]
    elif invalid == "keywords":
        brief["keywords"] = ["different", "keywords"]
    text = "not a JSON brief" if invalid == "json" else json.dumps(brief)
    mock_api[1].append(httpx.Response(200, json=completion(text)))

    with pytest.raises(ValueError):
        asyncio.run(director.run_creative_director(tmp_path))

    assert not list(tmp_path.rglob("agent-output.json"))


@pytest.mark.parametrize("explicit", [None, "test/explicit-model"])
def test_model_configuration_uses_environment_unless_explicitly_overridden(
    director, brief, mock_api, monkeypatch, tmp_path, explicit
):
    monkeypatch.setenv("HF_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("HF_MODEL", "test/environment-model")
    mock_api[1].append(httpx.Response(200, json=completion(json.dumps(brief))))

    result = asyncio.run(director.run_creative_director(tmp_path, model=explicit))

    expected = explicit or "test/environment-model"
    assert json.loads(mock_api[0][0].content)["model"] == expected
    record = json.loads(Path(result["record_path"]).read_text(encoding="utf-8"))
    assert record["model_events"][0]["model"] == expected


def test_offline_mode_needs_no_token_and_makes_no_model_call(director, mock_api, tmp_path):
    result = asyncio.run(director.run_creative_director(tmp_path, offline=True))

    assert mock_api[0] == []
    record = json.loads(Path(result["record_path"]).read_text(encoding="utf-8"))
    assert record["usage"]["model_calls"] == 0
    assert record["model_events"] == []
    reference = files("creative") / "resources" / "sample-output.json"
    assert result["output"] == json.loads(reference.read_text(encoding="utf-8"))


def test_custom_request_sends_keywords_and_constraints_to_model(
    director, brief, mock_api, monkeypatch, tmp_path
):
    monkeypatch.setenv("HF_TOKEN", TEST_TOKEN)
    custom_request = {
        "keywords": ["night market", "community", "lanterns"],
        "constraints": {
            "tone": "welcoming",
            "palette": ["warm amber", "midnight blue"],
        },
    }
    request_path = tmp_path / "poster-request.json"
    request_path.write_text(json.dumps(custom_request), encoding="utf-8")
    brief["keywords"] = custom_request["keywords"]
    brief["visual_direction"]["mood"] = custom_request["constraints"]["tone"]
    brief["palette"]["colors"] = custom_request["constraints"]["palette"]
    mock_api[1].append(httpx.Response(200, json=completion(json.dumps(brief))))

    result = asyncio.run(
        director.run_creative_director(tmp_path / "run", request_path=request_path)
    )

    request_body = json.loads(mock_api[0][0].content)
    user_text = "\n".join(
        message["content"]
        for message in request_body["messages"]
        if message["role"] == "user"
    )
    for text in ("night market", "community", "lanterns", "welcoming", "warm amber"):
        assert text in user_text
    assert result["output"]["content"] == brief
    record = json.loads(Path(result["record_path"]).read_text(encoding="utf-8"))
    assert [item["text"] for item in record["original_request"]["keywords"]] == (
        custom_request["keywords"]
    )
    assert {
        item["name"]: item["value"] for item in record["original_request"]["constraints"]
    } == custom_request["constraints"]


def test_default_prompt_is_sent_verbatim_to_hugging_face(
    director, brief, mock_api, monkeypatch, tmp_path
):
    monkeypatch.setenv("HF_TOKEN", TEST_TOKEN)
    mock_api[1].append(httpx.Response(200, json=completion(json.dumps(brief))))

    asyncio.run(director.run_creative_director(tmp_path))

    body = json.loads(mock_api[0][0].content)
    system_messages = [message for message in body["messages"] if message["role"] == "system"]
    assert system_messages == [
        {"role": "system", "content": director.load_prompt()}
    ]


@pytest.mark.parametrize("changed",[False,True])
def test_real_captured_keyword_object_shape_is_bound_only_when_text_matches(director,mock_api,monkeypatch,tmp_path,changed):
    monkeypatch.setenv("HF_TOKEN",TEST_TOKEN)
    captured=json.loads((Path(__file__).parent/"fixtures/a1-keyword-objects.json").read_text(encoding="utf-8"))
    if changed:captured["keywords"][1]["text"]="unrelated"
    mock_api[1].append(httpx.Response(200,json=completion(json.dumps(captured))))
    request=tmp_path/"request.json"
    request.write_text(json.dumps({"keywords":["ocean","plastic","hope"]}),encoding="utf-8")
    if changed:
        with pytest.raises(ValueError):asyncio.run(director.run_creative_director(tmp_path/"run",request_path=request))
    else:
        result=asyncio.run(director.run_creative_director(tmp_path/"run",request_path=request))
        assert result["output"]["content"]["keywords"]==["ocean","plastic","hope"]
        assert result["output"]["content"]["copy"]==captured["copy"]
        raw=json.loads(next(tmp_path.rglob("model-response.json")).read_text(encoding="utf-8"))
        assert json.loads(raw["raw_text"])["keywords"]==captured["keywords"]
        validation=json.loads(next(tmp_path.rglob("validation.json")).read_text(encoding="utf-8"))
        assert validation["normalized_fields"]==["keywords"]
    assert len(mock_api[0])==1

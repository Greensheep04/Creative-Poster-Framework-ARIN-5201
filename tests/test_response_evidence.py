import asyncio
import json
from importlib.resources import files
import pytest
from creative.response_evidence import redact_response_text


@pytest.mark.parametrize("field",["token","api_key","Authorization","secret","nested_access_token","password"])
def test_credential_json_fields_mask_opaque_values_without_discarding_diagnostics(field):
    raw=json.dumps({"nested":{field:"opaque-other-credential"},"explanation":"Keep this detail."})
    safe=redact_response_text(raw)
    assert "opaque-other-credential" not in safe
    assert json.loads(safe)["explanation"]=="Keep this detail."
    assert json.loads(safe)["nested"][field]=="[REDACTED]"


def test_unknown_schema_key_cannot_echo_inline_credential_in_exception(tmp_path,monkeypatch):
    import creative.creative_director as cd
    secret="opaque-actual-credential-123"
    monkeypatch.setenv("HF_TOKEN",secret)
    brief=json.loads((files("creative")/"resources/sample-output.json").read_text(encoding="utf-8"))["content"]
    brief[secret]="Unexpected model field."
    async def reply(*args,**kwargs):return json.dumps(brief)
    monkeypatch.setattr(cd,"call_hugging_face",reply)
    with pytest.raises(ValueError) as error:asyncio.run(cd.run_creative_director(tmp_path))
    assert secret not in str(error.value)
    for p in tmp_path.rglob("*.json"):assert secret not in p.read_text(encoding="utf-8")

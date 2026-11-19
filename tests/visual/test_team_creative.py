import asyncio
import hashlib
import json
from copy import deepcopy
import pytest
from poster_framework import OriginalRequest, run_single_agent_from_request
from poster_visual_producer.contracts import CreativeBrief
from poster_visual_producer.team_creative import creative_registration


def test_actual_offline_a1_publishes_versioned_brief_and_original_source(tmp_path):
    request = OriginalRequest.from_keywords("team-a1", ["ocean", "plastic", "hope"])
    result = asyncio.run(run_single_agent_from_request(creative_registration(offline=True), request, root=tmp_path))
    output = result.agent_result.output
    brief = CreativeBrief.model_validate(output.content)
    assert output.content_format.version == "0.1.0"
    assert brief.run_id == request.run_id and brief.request_ref == request.request_id
    assert brief.brief_version == 1
    raw = brief.reference_notes[0]["original_a1"]
    assert raw["keywords"] == ["ocean", "plastic", "hope"]
    assert hashlib.sha256(json.dumps(raw,ensure_ascii=False,allow_nan=False,sort_keys=True).encode("utf-8")).hexdigest() == brief.reference_notes[0]["source_sha256"]
    assert brief.copy_blocks["title"].text == raw["copy"]["headline"]
    assert "cta" not in brief.copy_blocks
    assert brief.intent["palette"] == raw["palette"]
    refs = [a for a in output.artifacts if a.media_type == "application/vnd.poster.a1-source+json"]
    assert len(refs) == 1 and refs[0].sha256 == brief.reference_notes[0]["source_sha256"]


def test_adapter_rejects_changed_keywords_before_publishing(monkeypatch, tmp_path):
    from creative.creative_director import OfflineCreativeDirectorAgent
    original = OfflineCreativeDirectorAgent.run
    async def changed(self, inputs, context):
        output = await original(self, inputs, context)
        data = deepcopy(output.content); data["keywords"] = ["different", "keywords"]
        return output.model_copy(update={"content": data})
    monkeypatch.setattr(OfflineCreativeDirectorAgent, "run", changed)
    request = OriginalRequest.from_keywords("team-bad", ["ocean", "hope"])
    with pytest.raises(ValueError, match="keywords"):
        asyncio.run(run_single_agent_from_request(creative_registration(offline=True), request, root=tmp_path))

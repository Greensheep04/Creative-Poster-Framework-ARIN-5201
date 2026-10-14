import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def module():
    path = ROOT / "examples/visual/cd_direct_poster.py"
    assert path.is_file(), "Full-CD direct SDXL poster experiment is missing"
    spec = importlib.util.spec_from_file_location("cd_direct_poster", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class Tokenizer:
    model_max_length = 7
    bos_token_id, eos_token_id, pad_token_id = 101, 102, 0

    def __call__(self, text, **kwargs):
        return {"input_ids": list(range(1, len(text.split()) + 1))}


def test_full_prompt_contains_exact_copy_and_all_cd_information():
    m = module()
    for row in m.load_source_cases(ROOT):
        prompt, negative = m.build_prompt(row["cd_output"])
        cd = row["cd_output"]
        assert cd["headline"] in prompt
        assert cd.get("supporting_copy", "") in prompt
        for field in ("concept", "visual_direction"):
            assert cd[field] in prompt
        for mapping in cd["keyword_mapping"]:
            assert mapping["keyword"] in prompt
            assert mapping["representation"] in prompt
        for values in cd["style"].values():
            for value in values if isinstance(values, list) else [values]:
                assert value in prompt
        for value in cd["must_include"] + cd["must_avoid"]:
            assert value in prompt
        assert "text, letters" not in negative
        if not cd.get("supporting_copy"):
            assert "headline only" in prompt


def test_chunking_keeps_every_token_and_pads_negative_to_equal_length():
    m = module()
    positive = m.chunk_tokens(Tokenizer(), "word " * 17)
    negative = m.chunk_tokens(Tokenizer(), "bad", count=len(positive["chunks"]))
    assert len(positive["chunks"]) == len(negative["chunks"]) == 4
    recovered = []
    for ids, count in zip(positive["chunks"], positive["content_counts"]):
        assert len(ids) == 7 and ids[0] == 101 and ids[count + 1] == 102
        recovered.extend(ids[1:count + 1])
    assert recovered == list(range(1, 18))
    assert negative["content_counts"] == [1, 0, 0, 0]


def test_native_control_retains_exact_copy_and_rejects_overlong_input():
    m = module()
    rows = m.load_source_cases(ROOT)
    prompts = ROOT / "examples/visual/cd-direct-native-prompts.json"
    for row in rows:
        m.apply_native_prompt(row, prompts)
        assert row["cd_output"]["headline"] in row["prompt"]
        if row["cd_output"].get("supporting_copy"):
            assert row["cd_output"]["supporting_copy"] in row["prompt"]
        assert row["conversion_mode"] == "authored_compact_poster_prompts_with_exact_CD_copy"
    with pytest.raises(ValueError, match="native token limit"):
        m.native_analysis((Tokenizer(), Tokenizer()), "word " * 6, "bad")


def test_comparison_labels_distinguish_compact_native_from_full_cd():
    m = module()
    assert "compact" in m.comparison_label({"conditioning": {"encoding": "native_single_CLIP_window"}})
    assert "full CD" in m.comparison_label({"conditioning": {"encoding": "concatenated_CLIP_chunks_with_token_weighted_pooled_mean"}})


def test_two_encoder_conditioning_has_matching_shapes_and_weighted_pool():
    torch = pytest.importorskip("torch")
    m = module()

    class Encoder:
        def __init__(self, dimension):
            self.dimension = dimension

        def __call__(self, ids, **kwargs):
            hidden = ids.float().unsqueeze(-1).repeat(1, 1, self.dimension)
            return SimpleNamespace(hidden_states=(hidden * 0, hidden, hidden * 2),
                                   text_embeds=ids[:, 1].float().unsqueeze(-1).repeat(1, self.dimension))

    pipe = SimpleNamespace(tokenizer=Tokenizer(), tokenizer_2=Tokenizer(),
                           text_encoder=Encoder(2), text_encoder_2=Encoder(3))
    conditioning, metadata = m.encode_conditioning(pipe, "word " * 12, "bad", "cpu")
    assert tuple(conditioning["prompt_embeds"].shape) == (1, 21, 5)
    assert conditioning["prompt_embeds"].shape == conditioning["negative_prompt_embeds"].shape
    assert tuple(conditioning["pooled_prompt_embeds"].shape) == (1, 3)
    # Three chunks begin at tokens 1, 6, 11; weights are 5, 5, 2.
    assert torch.allclose(conditioning["pooled_prompt_embeds"], torch.full((1, 3), 57 / 12))
    assert metadata["positive_token_counts"] == [12, 12]
    assert metadata["chunks"] == 3


def test_reference_source_drift_is_rejected(tmp_path):
    m = module()
    row = m.load_source_cases(ROOT)[0]
    (tmp_path / "report.json").write_text(json.dumps({"status": "rendered", "cases": [{
        "id": row["id"], "cd_source_sha256": "0" * 64,
        "poster": "poster.png", "poster_sha256": "0" * 64
    }]}))
    with pytest.raises(ValueError, match="CD source"):
        m.prepare_cases(ROOT, tmp_path)


def test_raw_direct_output_is_saved_without_text_replacement(tmp_path):
    m = module()
    row = m.load_source_cases(ROOT)[0]
    buffer = io.BytesIO()
    Image.new("RGB", (720, 1280), "#315078").save(buffer, "PNG")
    raw = buffer.getvalue()
    prompt, negative = m.build_prompt(row["cd_output"])
    case = {**row, "prompt": prompt, "negative_prompt": negative, "reference_bytes": raw}

    class Backend:
        def analyze(self, prompt, negative):
            return {"positive_token_counts": [180, 180], "negative_token_counts": [15, 15], "chunks": 3}

        def generate(self, prompt, negative, seed):
            return raw, {"actual_seed": seed}

    result = m.run_experiment([case], Backend(), tmp_path / "out")
    assert result["image_operations"] == 1
    assert result["cases"][0]["model_text_retouched"] is False
    assert (tmp_path / "out" / row["id"] / "direct.png").read_bytes() == raw
    assert (tmp_path / "out" / row["id"] / "pillow.png").read_bytes() == raw

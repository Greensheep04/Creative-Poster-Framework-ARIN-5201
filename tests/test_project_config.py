import json
from pathlib import Path
import pytest
from poster_framework.project_config import load_project_config, ConfigurationError

ROOT=Path(__file__).resolve().parents[1]

def test_complete_blank_key_template_and_relative_paths():
    cfg=load_project_config(ROOT/"config/project.example.toml")
    assert cfg.runtime.mode=="offline" and cfg.agents.a1.api_key.get_secret_value()==""
    assert cfg.fonts["noto_sans_sc"]["400"].path==ROOT/"resources/fonts/NotoSansCJKsc-Regular.otf"
    assert cfg.budget.max_model_calls==6
    assert "A1" in str(cfg.live_missing()) and "A4" in str(cfg.live_missing())

def test_inline_keys_are_redacted_in_repr_dump_and_errors(tmp_path):
    secret="sk-unified-sentinel-123456789"
    text=(ROOT/"config/project.example.toml").read_text(encoding="utf-8").replace('api_key = ""',f'api_key = "{secret}"')
    path=tmp_path/"local.toml";path.write_text(text,encoding="utf-8")
    cfg=load_project_config(path)
    assert cfg.agents.a1.api_key.get_secret_value()==secret
    assert secret not in repr(cfg) and secret not in json.dumps(cfg.redacted())
    path.write_text(text.replace('max_model_calls = 6',f'max_model_calls = "{secret}"'),encoding="utf-8")
    with pytest.raises(ConfigurationError) as err:load_project_config(path)
    assert secret not in str(err.value)
    path.write_text(f'api_key = "{secret}\n',encoding="utf-8")
    with pytest.raises(ConfigurationError) as err:load_project_config(path)
    assert secret not in str(err.value)

def test_unknown_fields_are_rejected(tmp_path):
    text=(ROOT/"config/project.example.toml").read_text(encoding="utf-8")
    path=tmp_path/"bad.toml";path.write_text('unexpected = true\n'+text,encoding="utf-8")
    with pytest.raises(ConfigurationError):load_project_config(path)


@pytest.mark.parametrize("suffix", ["\n","\r\n","\t"," ","\u2603"])
def test_malformed_inline_keys_are_rejected_without_echoing_values(tmp_path,suffix):
    secret="sk-malformed-sentinel"+suffix
    text=(ROOT/"config/project.example.toml").read_text(encoding="utf-8").replace('api_key = ""','api_key = '+json.dumps(secret,ensure_ascii=False))
    path=tmp_path/"bad-key.toml";path.write_text(text,encoding="utf-8")
    with pytest.raises(ConfigurationError) as error:load_project_config(path)
    assert "sk-malformed-sentinel" not in str(error.value)

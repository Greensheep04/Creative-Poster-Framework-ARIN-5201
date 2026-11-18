"""Strict local TOML configuration; credentials stay private to client construction."""
from __future__ import annotations
import copy
import tomllib
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from .types import RunBudget


class ConfigurationError(ValueError):
    pass


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class Credentials(Section):
    api_key: SecretStr = Field(default=SecretStr(""), repr=False)

    @field_validator("api_key")
    @classmethod
    def printable_key(cls, value):
        if any(ord(c)<33 or ord(c)>126 for c in value.get_secret_value()):
            raise ValueError("API key must contain printable ASCII without whitespace")
        return value


class Runtime(Section):
    mode: Literal["offline", "live"] = "offline"
    output_root: Path = Path("../runs/unified")
    keywords: list[str] = Field(default_factory=lambda:["ocean","plastic","hope"], min_length=2, max_length=5)
    constraints: dict = Field(default_factory=dict)
    seed: int = Field(default=42, ge=0)

    @field_validator("keywords")
    @classmethod
    def valid_keywords(cls, values):
        if any(not v.strip() for v in values): raise ValueError("keywords must contain text")
        return values


class A1(Credentials):
    model: str = Field(default="Qwen/Qwen3-4B-Instruct-2507", min_length=1)
    endpoint: str = "https://router.huggingface.co/featherless-ai/v1/chat/completions"
    api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    max_tokens: int = Field(default=2048, ge=128, le=8192)
    temperature: float = Field(default=0.7, ge=0, le=2, allow_inf_nan=False)
    timeout_seconds: float = Field(default=120, gt=0, le=600)

    @field_validator("endpoint")
    @classmethod
    def valid_endpoint(cls, value):
        url=urlsplit(value)
        if url.scheme!="https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("endpoint must be HTTPS without inline credentials")
        return value


class A2(Section):
    template_id: Literal["framed_hero_v1","full_bleed_v1","split_stack_v1"] | None = None
    font_family_key: str = Field(default="noto_sans_sc", min_length=1)
    adaptive: bool = True


class A3(Credentials):
    api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    backend: dict

    @field_validator("backend")
    @classmethod
    def valid_backend(cls, value):
        from poster_visual_producer.backends import SDXLConfig
        from poster_visual_producer.qwen_backend import QwenImageConfig
        from poster_visual_producer.cloud_backend import CloudAPIConfig
        native=dict(value); kind=native.pop("kind",None)
        schemas={"sdxl":SDXLConfig,"qwen_image":QwenImageConfig,"cloud_api":CloudAPIConfig}
        if kind not in schemas:raise ValueError("unsupported A3 backend")
        schemas[kind].model_validate(native)
        return value


class A4(Credentials):
    api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    provider: dict
    max_retries: int = Field(default=0, ge=0, le=1)
    request_timeout: float = Field(default=30, gt=0, le=300)
    total_timeout: float = Field(default=90, gt=0, le=600)

    @field_validator("provider")
    @classmethod
    def valid_provider(cls, value):
        from poster_critic.providers.chat import ProviderConfig
        ProviderConfig.model_validate(value)
        return value


class Agents(Section):
    a1: A1
    a2: A2
    a3: A3
    a4: A4


class Font(Section):
    path: Path
    index: int = Field(default=0, ge=0)


class ProjectConfig(Section):
    schema_version: Literal[1] = 1
    runtime: Runtime
    budget: RunBudget
    agents: Agents
    fonts: dict[str, dict[str, Font]]

    @field_validator("budget")
    @classmethod
    def bounded_trial(cls, value):
        if value.max_revision_rounds>1:raise ValueError("current integration supports at most one revision")
        return value

    def redact_text(self, text):
        for name in ("a1","a3","a4"):
            secret=getattr(self.agents,name).api_key.get_secret_value()
            if secret:text=text.replace(secret,"[REDACTED]")
        return text

    def redacted(self):
        value=self.model_dump(mode="json")
        secrets=[getattr(self.agents,n).api_key.get_secret_value() for n in ("a1","a3","a4")]
        def clean(item):
            if isinstance(item,dict):return {k:"[REDACTED]" if k=="api_key" else clean(v) for k,v in item.items()}
            if isinstance(item,list):return [clean(v) for v in item]
            if isinstance(item,str):
                for secret in secrets:
                    if secret:item=item.replace(secret,"[REDACTED]")
            return item
        return clean(value)

    def font_mapping(self):
        return {family:{weight:{"path":str(f.path),"index":f.index} for weight,f in weights.items()} for family,weights in self.fonts.items()}

    def live_missing(self):
        missing=[]
        if not self.agents.a1.api_key.get_secret_value().strip():missing.append("agents.a1.api_key (A1)")
        backend=self.agents.a3.backend
        if backend["kind"] in ("sdxl","qwen_image"):
            if not backend.get("model_dir"):missing.append("agents.a3.backend.model_dir (A3)")
        elif not self.agents.a3.api_key.get_secret_value().strip():missing.append("agents.a3.api_key (A3 cloud)")
        from poster_critic.providers.chat import ProviderConfig, valid_key_for_config
        config=ProviderConfig.model_validate(self.agents.a4.provider)
        if "YOUR" in config.base_url or "YOUR" in config.model:missing.append("agents.a4.provider.base_url/model (A4)")
        if not valid_key_for_config(config,self.agents.a4.api_key.get_secret_value()):missing.append("agents.a4.api_key (A4)")
        return missing


def load_project_config(path):
    try:
        path=Path(path).resolve()
        raw=tomllib.loads(path.read_text(encoding="utf-8-sig"))
        cfg=ProjectConfig.model_validate(raw)
        base=path.parent
        def absolute(value):return (base/value).resolve() if not value.is_absolute() else value.resolve()
        fonts={name:{weight:font.model_copy(update={"path":absolute(font.path)}) for weight,font in weights.items()} for name,weights in cfg.fonts.items()}
        if not fonts or cfg.agents.a2.font_family_key not in fonts:raise ValueError("missing selected font family")
        runtime=cfg.runtime.model_copy(update={"output_root":absolute(cfg.runtime.output_root)})
        backend=copy.deepcopy(cfg.agents.a3.backend)
        if backend.get("model_dir"):backend["model_dir"]=str(absolute(Path(backend["model_dir"])))
        return cfg.model_copy(update={"runtime":runtime,"fonts":fonts,"agents":cfg.agents.model_copy(update={"a3":cfg.agents.a3.model_copy(update={"backend":backend})})})
    except (ValueError,TypeError,OSError,KeyError):
        raise ConfigurationError("Invalid project configuration: check TOML/schema/paths; values are hidden") from None

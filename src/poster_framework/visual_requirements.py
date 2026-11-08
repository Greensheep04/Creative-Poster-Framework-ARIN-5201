"""Versioned internal design contract, carried as an artifact, never a Spec extension."""
import hashlib
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

CONTRACT_MEDIA_TYPE = "application/vnd.poster.visual-contract+json"

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

class Source(Model):
    pointer: str
    quote: str
    sha256: str

class Requirement(Model):
    requirement_id: str
    category: Literal["subject", "relation", "scene", "style", "mood", "palette", "composition", "reading_order", "typography", "goal", "forbidden"]
    priority: Literal["required", "preferred", "optional", "forbidden"]
    source: Source
    entities: list[str] = Field(default_factory=list)
    predicates: list[str] = Field(default_factory=list)
    priority_basis: str = "source-field policy"

class VisualContract(Model):
    schema_version: Literal[1] = 1
    source_sha256: str
    requirements: list[Requirement]
    source_fields: list[str]
    issues: list[dict] = Field(default_factory=list)
    resolutions: list[dict] = Field(default_factory=list)

class IntentError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("Visual intent needs confirmation: " + "; ".join(x["code"] for x in issues))

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from .types import ArtifactReference, JSONValue, VersionDependency


class GenerationFingerprint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    weights: JSONValue
    prompt: str
    parameters: JSONValue
    reference_materials: tuple[ArtifactReference, ...]
    processing_config: JSONValue

    @classmethod
    def build(
        cls,
        *,
        provider: str,
        model: str,
        weights: JSONValue,
        prompt: str,
        parameters: JSONValue,
        reference_materials: Sequence[ArtifactReference],
        processing_config: JSONValue,
    ) -> GenerationFingerprint:
        return cls(
            provider=provider,
            model=model,
            weights=weights,
            prompt=prompt,
            parameters=parameters,
            reference_materials=tuple(reference_materials),
            processing_config=processing_config,
        )

    @property
    def key(self) -> str:
        value = {
            "provider": self.provider,
            "model": self.model,
            "weights": self.weights,
            "prompt": self.prompt,
            "parameters": self.parameters,
            "reference_sha256": [
                item.sha256.casefold() for item in self.reference_materials
            ],
            "processing_config": self.processing_config,
        }
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class ArtifactGenerationRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reference: ArtifactReference
    invocation_id: str = Field(min_length=1)
    fingerprint: GenerationFingerprint | None = None
    replaces: VersionDependency | None = None

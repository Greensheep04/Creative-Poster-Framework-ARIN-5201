from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, TypeAdapter

from .files import RunFileStore
from .generation import GenerationFingerprint
from .types import ArtifactReference, JSONValue


class CacheHit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: JSONValue
    artifacts: tuple[ArtifactReference, ...]


class _CacheEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: JSONValue
    artifacts: tuple[ArtifactReference, ...]


class GenerationCache:
    def __init__(self, file_store: RunFileStore) -> None:
        self._file_store = file_store
        self._entries: dict[str, _CacheEntry] = {}

    def get(self, fingerprint: GenerationFingerprint) -> CacheHit | None:
        if not isinstance(fingerprint, GenerationFingerprint):
            raise TypeError("fingerprint must be a GenerationFingerprint")
        entry = self._entries.get(fingerprint.key)
        if entry is None:
            return None
        for reference in (*fingerprint.reference_materials, *entry.artifacts):
            self._file_store.read(reference)
        return CacheHit(
            value=entry.value,
            artifacts=tuple(item.model_copy(deep=True) for item in entry.artifacts),
        )

    def put(
        self,
        fingerprint: GenerationFingerprint,
        value: JSONValue,
        *,
        artifacts: Sequence[ArtifactReference] = (),
    ) -> None:
        if not isinstance(fingerprint, GenerationFingerprint):
            raise TypeError("fingerprint must be a GenerationFingerprint")
        artifact_rows = tuple(artifacts)
        for reference in (*fingerprint.reference_materials, *artifact_rows):
            self._file_store.read(reference)
        self._entries[fingerprint.key] = _CacheEntry(
            value=TypeAdapter(JSONValue).validate_python(value),
            artifacts=artifact_rows,
        )

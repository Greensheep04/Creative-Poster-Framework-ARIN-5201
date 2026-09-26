from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from uuid import uuid4

from .errors import AgentIdentityMismatchError, FileSecurityError
from .generation import ArtifactGenerationRecord, GenerationFingerprint
from .types import (
    AgentRole,
    ArtifactPurpose,
    ArtifactReference,
    ExecutionIdentity,
    VersionDependency,
)


class RunFileStore:
    def __init__(self, root: str | Path, run_id: str) -> None:
        if not isinstance(run_id, str) or not run_id or run_id in {".", ".."}:
            raise FileSecurityError("run_id cannot identify a safe directory")
        if "/" in run_id or "\\" in run_id:
            raise FileSecurityError("run_id cannot contain path separators")
        self._base = Path(root).resolve()
        self.run_id = run_id
        self._artifacts: dict[str, ArtifactReference] = {}
        self._generation_records: dict[
            tuple[str, str, str], ArtifactGenerationRecord
        ] = {}

    @property
    def artifacts(self) -> tuple[ArtifactReference, ...]:
        return tuple(item.model_copy(deep=True) for item in self._artifacts.values())

    @property
    def generation_records(self) -> tuple[ArtifactGenerationRecord, ...]:
        return tuple(
            item.model_copy(deep=True) for item in self._generation_records.values()
        )

    def generation_records_for_invocation(
        self,
        invocation_id: str,
    ) -> tuple[ArtifactGenerationRecord, ...]:
        if not isinstance(invocation_id, str) or not invocation_id:
            raise ValueError("invocation_id must be non-empty")
        return tuple(
            item.model_copy(deep=True)
            for item in self._generation_records.values()
            if item.invocation_id == invocation_id
        )

    def generation_record(
        self,
        reference: ArtifactReference,
        *,
        invocation_id: str | None = None,
    ) -> ArtifactGenerationRecord | None:
        self.read(reference)
        matching = [
            item
            for item in self._generation_records.values()
            if item.reference == reference
            and (invocation_id is None or item.invocation_id == invocation_id)
        ]
        if not matching:
            return None
        if invocation_id is not None:
            return matching[-1].model_copy(deep=True)
        origin = next(
            (item for item in matching if item.invocation_id == reference.source_id),
            None,
        )
        return (origin or matching[-1]).model_copy(deep=True)

    def scope(self, identity: ExecutionIdentity) -> ScopedRunFiles:
        if identity.run_id != self.run_id:
            raise AgentIdentityMismatchError("file identity belongs to another run")
        return ScopedRunFiles(self, identity)

    def write_bytes(
        self,
        identity: ExecutionIdentity,
        relative_path: str,
        data: bytes,
        *,
        purpose: ArtifactPurpose,
        media_type: str,
        generation_fingerprint: GenerationFingerprint | None = None,
        replaces: ArtifactReference | None = None,
    ) -> ArtifactReference:
        if identity.run_id != self.run_id:
            raise AgentIdentityMismatchError("file identity belongs to another run")
        if not isinstance(data, bytes):
            raise TypeError("file data must be bytes")
        if not isinstance(purpose, ArtifactPurpose):
            raise TypeError("file purpose must be an ArtifactPurpose")
        self._validate_generation_metadata(
            identity,
            purpose,
            generation_fingerprint,
            replaces,
        )
        if replaces is not None:
            self.read(replaces)
        destination = self._safe_relative_path(relative_path, must_exist=False)
        if destination.exists() or destination.is_symlink():
            raise FileSecurityError("file publication cannot overwrite an existing path")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination = self._safe_relative_path(relative_path, must_exist=False)
        if destination.exists() or destination.is_symlink():
            raise FileSecurityError("file publication cannot overwrite an existing path")
        destination.write_bytes(data)
        reference = ArtifactReference(
            artifact_id=f"artifact-{uuid4()}",
            run_id=self.run_id,
            revision=f"file-rev-{uuid4().hex}",
            role=identity.role,
            uri=f"runs/{self.run_id}/{PurePosixPath(relative_path).as_posix()}",
            sha256=hashlib.sha256(data).hexdigest(),
            media_type=media_type,
            purpose=purpose,
            source_id=identity.invocation_id,
        )
        self._artifacts[reference.artifact_id] = reference
        if generation_fingerprint is not None or replaces is not None:
            self._record_generation(
                identity,
                reference,
                generation_fingerprint,
                replaces,
            )
        return reference.model_copy(deep=True)

    @staticmethod
    def _validate_generation_metadata(
        identity: ExecutionIdentity,
        purpose: ArtifactPurpose,
        fingerprint: GenerationFingerprint | None,
        replaces: ArtifactReference | None,
    ) -> None:
        if fingerprint is not None and not isinstance(fingerprint, GenerationFingerprint):
            raise TypeError("generation_fingerprint must be a GenerationFingerprint")
        if replaces is not None and not isinstance(replaces, ArtifactReference):
            raise TypeError("replaces must be an ArtifactReference")
        if fingerprint is None and replaces is None:
            return
        if identity.role != AgentRole.visual_producer:
            raise AgentIdentityMismatchError(
                "only visual_producer may register asset generation metadata"
            )
        if purpose != ArtifactPurpose.asset:
            raise FileSecurityError("generation metadata requires an asset artifact")
        if replaces is not None and replaces.purpose != ArtifactPurpose.asset:
            raise FileSecurityError("replaced artifact must have asset purpose")

    def _record_generation(
        self,
        identity: ExecutionIdentity,
        reference: ArtifactReference,
        fingerprint: GenerationFingerprint | None,
        replaces: ArtifactReference | None,
    ) -> ArtifactGenerationRecord:
        if identity.run_id != self.run_id:
            raise AgentIdentityMismatchError("generation identity belongs to another run")
        self.read(reference)
        if (
            reference.role != AgentRole.visual_producer
            or reference.purpose != ArtifactPurpose.asset
        ):
            raise FileSecurityError("generation metadata requires a visual asset")
        if replaces is not None:
            self.read(replaces)
            if (
                replaces.role != AgentRole.visual_producer
                or replaces.purpose != ArtifactPurpose.asset
            ):
                raise FileSecurityError("replaced reference must identify a visual asset")
        target = (
            VersionDependency(
                artifact_id=replaces.artifact_id,
                revision=replaces.revision,
            )
            if replaces is not None
            else None
        )
        record = ArtifactGenerationRecord(
            reference=reference,
            invocation_id=identity.invocation_id,
            fingerprint=fingerprint,
            replaces=target,
        )
        key = (identity.invocation_id, reference.artifact_id, reference.revision)
        previous = self._generation_records.get(key)
        if previous is not None and previous != record:
            raise FileSecurityError(
                "an invocation cannot register conflicting asset generation metadata"
            )
        self._generation_records[key] = record
        return record.model_copy(deep=True)

    def read(self, reference: ArtifactReference) -> bytes:
        if not isinstance(reference, ArtifactReference):
            raise TypeError("file reference must be an ArtifactReference")
        if reference.run_id != self.run_id:
            raise FileSecurityError("file reference belongs to another run")
        registered = self._artifacts.get(reference.artifact_id)
        if registered is None or registered != reference:
            raise FileSecurityError("file reference was not issued by this run")
        if len(reference.sha256) != 64 or any(
            character not in "0123456789abcdefABCDEF" for character in reference.sha256
        ):
            raise FileSecurityError("file reference has an invalid SHA-256 value")
        relative_path = self._relative_from_uri(reference.uri)
        path = self._safe_relative_path(relative_path, must_exist=True)
        if not path.is_file():
            raise FileSecurityError("file reference does not point to a regular file")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != reference.sha256.casefold():
            raise FileSecurityError("file content hash does not match its reference")
        return data

    def reference(self, artifact_id: str) -> ArtifactReference:
        try:
            return self._artifacts[artifact_id].model_copy(deep=True)
        except KeyError as error:
            raise KeyError(f"unknown run artifact: {artifact_id}") from error

    def restore_reference(self, reference: ArtifactReference) -> None:
        if reference.run_id != self.run_id:
            raise FileSecurityError("restored file reference belongs to another run")
        self._artifacts[reference.artifact_id] = reference.model_copy(deep=True)

    def restore_generation_record(
        self,
        record: ArtifactGenerationRecord,
    ) -> None:
        if not isinstance(record, ArtifactGenerationRecord):
            raise TypeError("record must be an ArtifactGenerationRecord")
        self.read(record.reference)
        key = (
            record.invocation_id,
            record.reference.artifact_id,
            record.reference.revision,
        )
        previous = self._generation_records.get(key)
        if previous is not None and previous != record:
            raise FileSecurityError("restored generation metadata conflicts with this run")
        self._generation_records[key] = record.model_copy(deep=True)

    def _relative_from_uri(self, uri: str) -> str:
        prefix = f"runs/{self.run_id}/"
        if not uri.startswith(prefix):
            raise FileSecurityError("file URI is outside the current run")
        relative = uri[len(prefix) :]
        pure_path = PurePosixPath(relative)
        if (
            not relative
            or pure_path.is_absolute()
            or any(part in {"", ".", ".."} for part in pure_path.parts)
            or "\\" in relative
        ):
            raise FileSecurityError("file URI contains an invalid path")
        return pure_path.as_posix()

    def _safe_relative_path(self, relative_path: str, *, must_exist: bool) -> Path:
        if not isinstance(relative_path, str) or not relative_path:
            raise FileSecurityError("file path must be a non-empty relative path")
        pure_path = PurePosixPath(relative_path)
        if (
            pure_path.is_absolute()
            or any(part in {"", ".", ".."} for part in pure_path.parts)
            or "\\" in relative_path
        ):
            raise FileSecurityError("file path must stay within the current run")
        run_root = self._base / self.run_id
        if run_root.is_symlink():
            raise FileSecurityError("run storage directory cannot be a symbolic link")
        run_root.mkdir(parents=True, exist_ok=True)
        resolved_root = run_root.resolve(strict=True)
        try:
            resolved_root.relative_to(self._base.resolve(strict=True))
        except ValueError as error:
            raise FileSecurityError("run storage directory is outside the configured root") from error
        candidate = resolved_root.joinpath(*pure_path.parts)
        resolved_candidate = candidate.resolve(strict=must_exist)
        try:
            resolved_candidate.relative_to(resolved_root)
        except ValueError as error:
            raise FileSecurityError("resolved file path is outside the current run") from error
        if resolved_candidate == resolved_root:
            raise FileSecurityError("file path must identify a file")
        return resolved_candidate


class ScopedRunFiles:
    def __init__(self, store: RunFileStore, identity: ExecutionIdentity) -> None:
        self._store = store
        self._identity = identity

    def write_bytes(
        self,
        relative_path: str,
        data: bytes,
        *,
        purpose: ArtifactPurpose,
        media_type: str,
        generation_fingerprint: GenerationFingerprint | None = None,
        replaces: ArtifactReference | None = None,
    ) -> ArtifactReference:
        return self._store.write_bytes(
            self._identity,
            relative_path,
            data,
            purpose=purpose,
            media_type=media_type,
            generation_fingerprint=generation_fingerprint,
            replaces=replaces,
        )

    def register_generation(
        self,
        reference: ArtifactReference,
        fingerprint: GenerationFingerprint,
    ) -> ArtifactGenerationRecord:
        if not isinstance(reference, ArtifactReference):
            raise TypeError("reference must be an ArtifactReference")
        self._store._validate_generation_metadata(
            self._identity,
            reference.purpose,
            fingerprint,
            None,
        )
        return self._store._record_generation(
            self._identity,
            reference,
            fingerprint,
            None,
        )

    def generation_record(
        self,
        reference: ArtifactReference,
    ) -> ArtifactGenerationRecord | None:
        return self._store.generation_record(reference)

    def invocation_path(self, filename: str) -> str:
        if not isinstance(filename, str) or not filename:
            raise FileSecurityError("invocation filename must be a non-empty relative path")
        if "\\" in filename:
            raise FileSecurityError("invocation filename must use relative POSIX paths")
        parts = filename.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise FileSecurityError("invocation filename must stay within its invocation")
        invocation_id = self._identity.invocation_id
        if (
            not invocation_id
            or "/" in invocation_id
            or "\\" in invocation_id
            or invocation_id in {".", ".."}
        ):
            raise FileSecurityError("invocation identity cannot identify a safe directory")
        path = PurePosixPath("invocations") / self._identity.role.value / invocation_id
        return (path / PurePosixPath(filename)).as_posix()

    def read(self, reference: ArtifactReference) -> bytes:
        if reference.run_id != self._identity.run_id:
            raise FileSecurityError("file reference belongs to another run")
        return self._store.read(reference)

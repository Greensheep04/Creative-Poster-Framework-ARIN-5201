from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .errors import AgentIdentityMismatchError
from .types import (
    AgentRole,
    ArtifactPurpose,
    ArtifactReference,
    ContentFormat,
    ExecutionIdentity,
    JSONValue,
    OriginalRequest,
)


class StoredArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    reference: ArtifactReference
    content: JSONValue
    content_format: ContentFormat


class RoleNote(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    note_id: str = Field(min_length=1)
    role: AgentRole
    content: JSONValue
    based_on: dict[str, str]


class RevisionHistoryRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    revision_id: str = Field(min_length=1)
    role: AgentRole | None = None
    changes: JSONValue
    unresolved_issues: tuple[str, ...] = ()


class MemoryReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(min_length=1)
    reference_id: str = Field(min_length=1)


class TaskMemory:
    def __init__(self, original_request: OriginalRequest) -> None:
        if not isinstance(original_request, OriginalRequest):
            raise TypeError("original_request must be an OriginalRequest")
        self._original_request = original_request.model_copy(deep=True)
        self._artifacts: dict[str, StoredArtifact] = {}
        self._current_versions: dict[str, str] = {}
        self._notes: dict[str, RoleNote] = {}
        self._revisions: list[RevisionHistoryRecord] = []
        self._unresolved_issues: tuple[str, ...] = ()
        self._private_sessions: dict[tuple[str, AgentRole, str], list[JSONValue]] = {}
        self._references: dict[str, tuple[Literal["revision", "note"], str]] = {}

    @property
    def run_id(self) -> str:
        return self._original_request.run_id

    def scope(
        self,
        identity: ExecutionIdentity,
        *,
        reference_validator: Callable[[ArtifactReference], object] | None = None,
    ) -> ScopedTaskMemory:
        if identity.run_id != self.run_id:
            raise AgentIdentityMismatchError("memory identity belongs to another run")
        return ScopedTaskMemory(self, identity, reference_validator)

    def publish_artifact(
        self,
        name: str,
        reference: ArtifactReference,
        content: JSONValue,
        content_format: ContentFormat,
        *,
        publishing_identity: ExecutionIdentity,
    ) -> None:
        if not isinstance(publishing_identity, ExecutionIdentity):
            raise TypeError("publishing_identity must be an ExecutionIdentity")
        if publishing_identity.run_id != self.run_id:
            raise AgentIdentityMismatchError("artifact publisher belongs to another run")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("artifact memory name must be non-empty")
        if reference.run_id != self.run_id:
            raise AgentIdentityMismatchError("artifact belongs to another run")
        if reference.role != publishing_identity.role:
            raise AgentIdentityMismatchError(
                "artifact reference role must match the publishing role"
            )
        previous = self._artifacts.get(name)
        if previous is not None and previous.reference.role != publishing_identity.role:
            raise AgentIdentityMismatchError(
                "artifact name belongs to another publishing role"
            )
        checked_content = TypeAdapter(JSONValue).validate_python(content)
        artifact = StoredArtifact(
            name=name,
            reference=reference,
            content=copy.deepcopy(checked_content),
            content_format=content_format,
        )
        if previous is not None and previous.reference.artifact_id != reference.artifact_id:
            previous_is_shared = any(
                item.name != name
                and item.reference.artifact_id == previous.reference.artifact_id
                for item in self._artifacts.values()
            )
            if not previous_is_shared:
                self._current_versions.pop(previous.reference.artifact_id, None)
        self._artifacts[name] = artifact
        self._current_versions[reference.artifact_id] = reference.revision

    def invalidate_artifact(self, name: str) -> StoredArtifact | None:
        try:
            artifact = self._artifacts.pop(name)
        except KeyError:
            return None
        still_current = any(
            item.reference.artifact_id == artifact.reference.artifact_id
            for item in self._artifacts.values()
        )
        if not still_current:
            self._current_versions.pop(artifact.reference.artifact_id, None)
        return artifact.model_copy(deep=True)

    def add_note(
        self,
        *,
        note_id: str,
        role: AgentRole,
        content: JSONValue,
        based_on: Mapping[str, str],
    ) -> None:
        if note_id in self._notes:
            raise ValueError(f"role note identity already exists: {note_id}")
        if not isinstance(role, AgentRole):
            raise TypeError("role note requires an AgentRole")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in based_on.items()):
            raise TypeError("note version dependencies must map artifact IDs to revisions")
        self._notes[note_id] = RoleNote(
            note_id=note_id,
            role=role,
            content=copy.deepcopy(TypeAdapter(JSONValue).validate_python(content)),
            based_on=dict(based_on),
        )

    def set_unresolved_issues(self, issues: Sequence[str]) -> None:
        if any(not isinstance(issue, str) or not issue.strip() for issue in issues):
            raise ValueError("unresolved issues must be non-empty strings")
        self._unresolved_issues = tuple(issues)

    def add_revision(
        self,
        *,
        revision_id: str,
        role: AgentRole | None,
        changes: JSONValue,
        unresolved_issues: Sequence[str],
    ) -> RevisionHistoryRecord:
        if any(item.revision_id == revision_id for item in self._revisions):
            raise ValueError(f"revision identity already exists: {revision_id}")
        self.set_unresolved_issues(unresolved_issues)
        record = RevisionHistoryRecord(
            revision_id=revision_id,
            role=role,
            changes=copy.deepcopy(TypeAdapter(JSONValue).validate_python(changes)),
            unresolved_issues=tuple(unresolved_issues),
        )
        self._revisions.append(record)
        return record

    def history_reference(self, revision_id: str) -> MemoryReference:
        if not any(item.revision_id == revision_id for item in self._revisions):
            raise KeyError(f"unknown revision history entry: {revision_id}")
        reference_id = uuid4().hex
        self._references[reference_id] = ("revision", revision_id)
        return MemoryReference(run_id=self.run_id, reference_id=reference_id)

    def note_reference(self, note_id: str) -> MemoryReference:
        if note_id not in self._notes:
            raise KeyError(f"unknown role note: {note_id}")
        reference_id = uuid4().hex
        self._references[reference_id] = ("note", note_id)
        return MemoryReference(run_id=self.run_id, reference_id=reference_id)

    def read_history(self, reference: MemoryReference) -> RevisionHistoryRecord:
        resource_kind, resource_id = self._resolve_reference(reference)
        if resource_kind != "revision":
            raise ValueError("memory reference does not point to revision history")
        return next(item.model_copy(deep=True) for item in self._revisions if item.revision_id == resource_id)

    def read_note(self, reference: MemoryReference) -> RoleNote:
        resource_kind, resource_id = self._resolve_reference(reference)
        if resource_kind != "note":
            raise ValueError("memory reference does not point to a role note")
        return self._notes[resource_id].model_copy(deep=True)

    def _resolve_reference(
        self,
        reference: MemoryReference,
    ) -> tuple[Literal["revision", "note"], str]:
        if reference.run_id != self.run_id:
            raise AgentIdentityMismatchError("memory reference belongs to another run")
        try:
            return self._references[reference.reference_id]
        except KeyError as error:
            raise KeyError("memory reference was not issued by this run") from error

    def context_for(self, identity: ExecutionIdentity) -> JSONValue:
        if identity.run_id != self.run_id:
            raise AgentIdentityMismatchError("memory context belongs to another run")
        visible_roles = {
            AgentRole.creative_director: {AgentRole.creative_director},
            AgentRole.layout_planner: {
                AgentRole.creative_director,
                AgentRole.layout_planner,
            },
            AgentRole.visual_producer: {
                AgentRole.creative_director,
                AgentRole.layout_planner,
            },
            AgentRole.quality_critic: set(AgentRole),
        }[identity.role]
        artifacts = []
        for artifact in self._artifacts.values():
            role_visible = artifact.reference.role in visible_roles
            producer_asset = (
                identity.role == AgentRole.visual_producer
                and artifact.reference.role == AgentRole.visual_producer
                and artifact.reference.purpose == ArtifactPurpose.asset
            )
            if role_visible or producer_asset:
                artifacts.append(artifact.model_dump(mode="json"))
        current_notes = [
            note.model_dump(mode="json")
            for note in self._notes.values()
            if note.role == identity.role
            and all(
                self._current_versions.get(artifact_id) == revision
                for artifact_id, revision in note.based_on.items()
            )
        ]
        recent_revisions = [
            item.model_dump(mode="json")
            for item in self._revisions
            if item.role is None or item.role in visible_roles
        ][-2:]
        return {
            "original_request": self._original_request.model_dump(mode="json"),
            "artifacts": artifacts,
            "notes": current_notes,
            "unresolved_issues": list(self._unresolved_issues),
            "revision_history": recent_revisions,
            "private_messages": list(self.private_messages(identity)),
        }

    def append_private_message(
        self,
        identity: ExecutionIdentity,
        message: JSONValue,
    ) -> None:
        key = (identity.run_id, identity.role, identity.invocation_id)
        checked = TypeAdapter(JSONValue).validate_python(message)
        self._private_sessions.setdefault(key, []).append(copy.deepcopy(checked))

    def private_messages(self, identity: ExecutionIdentity) -> tuple[JSONValue, ...]:
        key = (identity.run_id, identity.role, identity.invocation_id)
        return tuple(copy.deepcopy(self._private_sessions.get(key, ())))

    def snapshot(self) -> JSONValue:
        return {
            "artifacts": [item.model_dump(mode="json") for item in self._artifacts.values()],
            "current_versions": dict(self._current_versions),
            "notes": [item.model_dump(mode="json") for item in self._notes.values()],
            "revisions": [item.model_dump(mode="json") for item in self._revisions],
            "unresolved_issues": list(self._unresolved_issues),
            "private_sessions": [
                {
                    "identity": {
                        "run_id": key[0],
                        "role": key[1].value,
                        "invocation_id": key[2],
                    },
                    "messages": copy.deepcopy(messages),
                }
                for key, messages in self._private_sessions.items()
            ],
            "references": [
                {
                    "reference_id": reference_id,
                    "resource_kind": resource[0],
                    "resource_id": resource[1],
                }
                for reference_id, resource in self._references.items()
            ],
        }

    def restore_snapshot(self, snapshot: JSONValue) -> None:
        if not isinstance(snapshot, Mapping):
            raise TypeError("task memory snapshot must be an object")
        self._artifacts = {}
        for item in snapshot["artifacts"]:
            artifact = StoredArtifact.model_validate(item)
            if artifact.reference.run_id != self.run_id:
                raise AgentIdentityMismatchError("saved artifact belongs to another run")
            self._artifacts[artifact.name] = artifact
        self._current_versions = dict(snapshot["current_versions"])
        self._notes = {
            note.note_id: note
            for note in (RoleNote.model_validate(item) for item in snapshot["notes"])
        }
        self._revisions = [
            RevisionHistoryRecord.model_validate(item) for item in snapshot["revisions"]
        ]
        self._unresolved_issues = tuple(snapshot["unresolved_issues"])
        self._private_sessions = {}
        for item in snapshot["private_sessions"]:
            identity = item["identity"]
            if identity["run_id"] != self.run_id:
                raise AgentIdentityMismatchError("saved private messages belong to another run")
            role = AgentRole(identity["role"])
            invocation_id = identity["invocation_id"]
            key = (self.run_id, role, invocation_id)
            self._private_sessions[key] = [
                copy.deepcopy(TypeAdapter(JSONValue).validate_python(message))
                for message in item["messages"]
            ]
        self._references = {}
        for item in snapshot["references"]:
            reference_id = item["reference_id"]
            resource_kind = item["resource_kind"]
            resource_id = item["resource_id"]
            if resource_kind not in {"revision", "note"}:
                raise ValueError("saved memory reference has an unknown resource kind")
            self._references[reference_id] = (resource_kind, resource_id)


class ScopedTaskMemory:
    def __init__(
        self,
        task_memory: TaskMemory,
        identity: ExecutionIdentity,
        reference_validator: Callable[[ArtifactReference], object] | None = None,
    ) -> None:
        self._task_memory = task_memory
        self._identity = identity
        self._reference_validator = reference_validator

    @property
    def private_messages(self) -> tuple[JSONValue, ...]:
        return self._task_memory.private_messages(self._identity)

    def append_private_message(self, message: JSONValue) -> None:
        self._task_memory.append_private_message(self._identity, message)

    def publish_artifact(
        self,
        name: str,
        reference: ArtifactReference,
        content: JSONValue,
        content_format: ContentFormat,
    ) -> None:
        if self._reference_validator is not None:
            self._reference_validator(reference)
        self._task_memory.publish_artifact(
            name,
            reference,
            content,
            content_format,
            publishing_identity=self._identity,
        )

    def add_note(
        self,
        *,
        note_id: str,
        content: JSONValue,
        based_on: Mapping[str, str],
        role: AgentRole | None = None,
    ) -> None:
        note_role = role or self._identity.role
        if note_role != self._identity.role:
            raise AgentIdentityMismatchError("context cannot create a note for another role")
        self._task_memory.add_note(
            note_id=note_id,
            role=note_role,
            content=content,
            based_on=based_on,
        )

    def set_unresolved_issues(self, issues: Sequence[str]) -> None:
        self._task_memory.set_unresolved_issues(issues)

    def add_revision(
        self,
        *,
        revision_id: str,
        changes: JSONValue,
        unresolved_issues: Sequence[str],
    ) -> RevisionHistoryRecord:
        return self._task_memory.add_revision(
            revision_id=revision_id,
            role=self._identity.role,
            changes=changes,
            unresolved_issues=unresolved_issues,
        )

    def context(self) -> JSONValue:
        return self._task_memory.context_for(self._identity)

    def history_reference(self, revision_id: str) -> MemoryReference:
        return self._task_memory.history_reference(revision_id)

    def note_reference(self, note_id: str) -> MemoryReference:
        return self._task_memory.note_reference(note_id)

    def read_history(self, reference: MemoryReference) -> RevisionHistoryRecord:
        return self._task_memory.read_history(reference)

    def read_note(self, reference: MemoryReference) -> RoleNote:
        return self._task_memory.read_note(reference)

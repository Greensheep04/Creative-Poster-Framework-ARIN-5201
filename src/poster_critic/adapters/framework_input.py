"""A4-owned conversion of explicitly mapped framework input and source bindings.

The caller supplies the decoder and a current-source authority. An input bundle
alone is never accepted as evidence that its candidate is still current.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
from typing import TypeVar

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ValidationError

from poster_framework.types import (
    INTERFACE_VERSION,
    AgentContext,
    AgentInput,
    AgentRole,
    ArtifactPurpose,
    ArtifactReference,
    ContentFormat,
    ExecutionIdentity,
    OriginalRequest,
    VersionDependency,
)

from ..contracts import (
    BASE_CHECKS,
    AssetContext,
    AuditInputV1,
    CheckId,
    Contract,
    CopyItem,
    ImagePayload,
    Keyword,
    LayoutContext,
    RenderContext,
    Text,
    UserConstraint,
)
from ..errors import InputError
from ..identity import fingerprint, input_fingerprint
from .local_input import MAX_IMAGE_BYTES

ModelT = TypeVar("ModelT", bound=BaseModel)


class AuditContextV1(Contract):
    """Decoded A4 context, without authority over the request or candidate."""

    approved_copy: tuple[CopyItem, ...] | None = None
    design_context: Text | None = None
    layout_context: LayoutContext | None = None
    asset_context: AssetContext | None = None
    render_context: RenderContext | None = None
    required_checks: tuple[CheckId, ...] = BASE_CHECKS


@dataclass(frozen=True, slots=True)
class CurrentAuditSources:
    """Current full source snapshot obtained from a caller-owned authority."""

    authority: str
    candidate: ArtifactReference
    version_dependencies: tuple[VersionDependency, ...]


@dataclass(frozen=True, slots=True)
class FrameworkInputMapping:
    """Explicit format decoder, candidate and synchronous current-source query."""

    source_format: ContentFormat
    candidate: ArtifactReference
    decode: Callable[[AgentInput], AuditContextV1]
    current_sources: Callable[[AgentContext], CurrentAuditSources]


@dataclass(frozen=True, slots=True)
class AuditBinding:
    """A4 private snapshot checked again before the caller delivers a report.

    Frozen outer fields do not freeze JSON values inside framework models.
    Stored hashes and contract revalidation detect those nested mutations.
    """

    identity: ExecutionIdentity
    source_format: ContentFormat
    candidate: ArtifactReference
    version_dependencies: tuple[VersionDependency, ...]
    authority: str
    original_request: OriginalRequest
    constraint_ids: tuple[str, ...]
    content_fingerprint: str
    input_fingerprint: str
    audit: AuditInputV1
    original_input: AgentInput = field(repr=False)
    _request_fingerprint: str = field(repr=False)
    _agent_input_fingerprint: str = field(repr=False)
    _source_fingerprint: str = field(repr=False)
    _decode: Callable[[AgentInput], AuditContextV1] = field(repr=False)
    _current_sources: Callable[[AgentContext], CurrentAuditSources] = field(repr=False)


def _model_copy(model: object, expected_type: type[ModelT], label: str) -> ModelT:
    if not isinstance(model, expected_type):
        raise InputError(f"{label} must be {expected_type.__name__}")
    try:
        # Reparse instead of trusting model_copy/model_construct bypasses.
        return expected_type.model_validate_json(model.model_dump_json())
    except (ValidationError, ValueError, TypeError, AttributeError) as error:
        raise InputError(f"invalid {label}") from error


def _dependencies(items: tuple[VersionDependency, ...]) -> tuple[tuple[str, str], ...]:
    if not isinstance(items, tuple):
        raise InputError("current version_dependencies must be an explicit tuple")
    dependencies = tuple(_model_copy(item, VersionDependency, "version dependency") for item in items)
    identities = [item.artifact_id for item in dependencies]
    if len(set(identities)) != len(identities):
        raise InputError("version dependencies contain duplicate or mixed artifact revisions")
    return tuple(sorted((item.artifact_id, item.revision) for item in dependencies))


def _source_value(candidate: ArtifactReference, dependencies: tuple[VersionDependency, ...],
                  authority: str) -> dict[str, object]:
    return {"authority": authority, "candidate": candidate.model_dump(mode="json"),
            "version_dependencies": _dependencies(dependencies)}


def _read_current(context: AgentContext, mapping: FrameworkInputMapping) -> CurrentAuditSources:
    if not callable(mapping.current_sources):
        raise InputError("an explicit current-source resolver is required")
    current = mapping.current_sources(context)
    if not isinstance(current, CurrentAuditSources):
        raise InputError("current-source resolver must return CurrentAuditSources")
    if not isinstance(current.authority, str) or not current.authority.strip():
        raise InputError("current-source authority must be named")
    candidate = _model_copy(current.candidate, ArtifactReference, "current candidate")
    _dependencies(current.version_dependencies)
    dependencies = tuple(_model_copy(item, VersionDependency, "current version dependency")
                         for item in current.version_dependencies)
    return CurrentAuditSources(current.authority, candidate, dependencies)


def _check_input(inputs: AgentInput, context: AgentContext,
                 mapping: FrameworkInputMapping) -> AgentInput:
    if not isinstance(mapping, FrameworkInputMapping) or not callable(mapping.decode):
        raise InputError("an explicit FrameworkInputMapping decoder is required")
    checked = _model_copy(inputs, AgentInput, "framework input")
    source_format = _model_copy(mapping.source_format, ContentFormat, "source format")
    candidate = _model_copy(mapping.candidate, ArtifactReference, "candidate reference")
    metadata = checked.metadata
    current_metadata = _model_copy(context.metadata, type(metadata), "context metadata")
    if metadata.interface_version != INTERFACE_VERSION or current_metadata.interface_version != INTERFACE_VERSION:
        raise InputError(f"A4 requires framework interface {INTERFACE_VERSION}")
    if metadata.identity.role != AgentRole.quality_critic or metadata.identity != context.identity:
        raise InputError("A4 input must use the current quality_critic execution identity")
    if current_metadata.identity != metadata.identity:
        raise InputError("context metadata identity does not match A4 input")
    request = _model_copy(context.original_request, OriginalRequest, "context original request")
    if metadata.original_request != request or current_metadata.original_request != request:
        raise InputError("A4 input and context must use the same original request")
    if checked.content_format != source_format:
        raise InputError("source format does not match the explicit A4 decoder")
    if candidate.run_id != metadata.identity.run_id or candidate.purpose != ArtifactPurpose.candidate:
        raise InputError("A4 candidate must be a candidate artifact in the current run")
    dependencies = _dependencies(metadata.version_dependencies)
    if dependencies != _dependencies(current_metadata.version_dependencies):
        raise InputError("input/context version dependencies do not match")
    if (candidate.artifact_id, candidate.revision) not in dependencies:
        raise InputError("candidate revision must be included in the complete version dependencies")
    matches = [item for item in metadata.files if item.file_id == candidate.artifact_id]
    if len(matches) > 1 or any(
        (item.run_id, item.uri, item.revision, item.sha256, item.media_type) !=
        (candidate.run_id, candidate.uri, candidate.revision, candidate.sha256, candidate.media_type)
        for item in matches
    ):
        raise InputError("listed candidate file does not match the explicit artifact reference")
    if checked.provenance is not None and checked.provenance.run_id != request.run_id:
        raise InputError("input provenance belongs to another run")
    return checked


def _check_current(inputs: AgentInput, mapping: FrameworkInputMapping,
                   current: CurrentAuditSources) -> None:
    if current.candidate != mapping.candidate:
        raise InputError("explicit candidate is stale or does not match the current source")
    if _dependencies(current.version_dependencies) != _dependencies(inputs.metadata.version_dependencies):
        raise InputError("version dependencies are stale or mix different current sources")


def _read_image(context: AgentContext, candidate: ArtifactReference) -> ImagePayload:
    try:
        data = context.files.read(candidate)
    except (OSError, ValueError, TypeError) as error:
        raise InputError("cannot read the registered A4 candidate") from error
    if not isinstance(data, bytes) or not data:
        raise InputError("candidate file must provide non-empty image bytes")
    if len(data) > MAX_IMAGE_BYTES:
        raise InputError("image exceeds the 20 MiB input limit")
    digest = sha256(data).hexdigest()
    if candidate.sha256 != digest:
        raise InputError("candidate image digest does not match its artifact reference")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in ("PNG", "JPEG"):
                raise InputError("candidate image must be a PNG or JPEG")
            if image.size != (720, 1280):
                raise InputError(f"candidate image must be 720x1280, got {image.size}")
            if getattr(image, "n_frames", 1) != 1:
                raise InputError("candidate image must contain a single frame")
            media_type = "image/png" if image.format == "PNG" else "image/jpeg"
            if candidate.media_type != media_type:
                raise InputError("candidate media_type does not match its real image bytes")
            image.load()
        return ImagePayload(data=data, media_type=media_type, sha256=digest)
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise InputError("cannot decode candidate image bytes") from error


def _constraints(request: OriginalRequest) -> tuple[UserConstraint, ...]:
    return tuple(UserConstraint(
        constraint_id="uc-" + fingerprint({"run_id": request.run_id, "position": position,
                                           "name": item.name, "value": item.value}),
        name=item.name, value=item.value,
    ) for position, item in enumerate(request.constraints))


def _normalize_copy(decoded: AuditContextV1,
                    constraints: tuple[UserConstraint, ...]) -> tuple[CopyItem, ...] | None:
    copies = list(decoded.approved_copy or ())
    for constraint in constraints:
        if constraint.name != "required_text":
            continue
        item = CopyItem(copy_id=f"constraint:{constraint.constraint_id}",
                        text=constraint.value, required=True)
        existing = next((copy for copy in copies if copy.copy_id == item.copy_id), None)
        if existing is not None and existing != item:
            raise InputError(f"copy ID conflicts with required_text: {item.copy_id}")
        if existing is None:
            copies.append(item)
    return tuple(copies) if copies else decoded.approved_copy


def _check_references(decoded: AuditContextV1, copies: tuple[CopyItem, ...] | None) -> None:
    copy_ids = {item.copy_id for item in copies or ()}
    elements = decoded.layout_context.elements if decoded.layout_context else ()
    element_ids = {item.element_id for item in elements}
    if any(item.copy_id is not None and item.copy_id not in copy_ids for item in elements):
        raise InputError("layout copy_id does not identify an approved copy item")
    if any(element_id not in element_ids
           for asset in (decoded.asset_context.assets if decoded.asset_context else ())
           for element_id in asset.element_ids):
        raise InputError("asset element reference does not identify a layout element")
    # Do not clip or reject invalid geometry here: deterministic QC reports it.


def build_audit_input(agent_input: AgentInput, context: AgentContext,
                      mapping: FrameworkInputMapping) -> tuple[AuditInputV1, AuditBinding]:
    """Normalize approved context while keeping original requirements authoritative."""
    checked = _check_input(agent_input, context, mapping)
    input_digest = fingerprint(checked.model_dump(mode="json"))
    current = _read_current(context, mapping)
    _check_current(checked, mapping, current)
    decoded = _model_copy(mapping.decode(checked.model_copy(deep=True)), AuditContextV1,
                          "decoded audit context")
    try:
        constraints = _constraints(checked.metadata.original_request)
        copies = _normalize_copy(decoded, constraints)
        _check_references(decoded, copies)
        image = _read_image(context, current.candidate)
        audit = AuditInputV1(
            sample_id=f"framework:{checked.metadata.identity.run_id}:{current.candidate.artifact_id}:{current.candidate.revision}",
            keywords=tuple(Keyword(keyword_id=item.keyword_id, text=item.text)
                           for item in checked.metadata.original_request.keywords),
            user_constraints=constraints, image=image,
            **{**decoded.model_dump(mode="python"), "approved_copy": copies},
        )
    except ValidationError as error:
        raise InputError("decoded A4 input does not satisfy the audit contract") from error
    after = _read_current(context, mapping)
    _check_current(checked, mapping, after)
    source_digest = fingerprint(_source_value(current.candidate, current.version_dependencies,
                                               current.authority))
    if source_digest != fingerprint(_source_value(after.candidate, after.version_dependencies, after.authority)):
        raise InputError("current-source authority changed while reading the candidate")
    if input_digest != fingerprint(_check_input(agent_input, context, mapping).model_dump(mode="json")):
        raise InputError("framework input changed while building the audit")
    binding = AuditBinding(
        identity=checked.metadata.identity, source_format=checked.content_format,
        candidate=current.candidate, version_dependencies=checked.metadata.version_dependencies,
        authority=current.authority, original_request=checked.metadata.original_request,
        constraint_ids=tuple(item.constraint_id for item in constraints),
        content_fingerprint=fingerprint(checked.content), input_fingerprint=input_fingerprint(audit),
        audit=audit, original_input=agent_input,
        _request_fingerprint=fingerprint(checked.metadata.original_request.model_dump(mode="json")),
        _agent_input_fingerprint=input_digest, _source_fingerprint=source_digest,
        _decode=mapping.decode, _current_sources=mapping.current_sources,
    )
    return audit, binding


def validate_binding_snapshot(binding: AuditBinding) -> AuditInputV1:
    """Validate private snapshots without reading files or querying an authority."""
    if not isinstance(binding, AuditBinding):
        raise InputError("delivery requires an A4 AuditBinding")
    checked = _model_copy(binding.original_input, AgentInput, "bound framework input")
    candidate = _model_copy(binding.candidate, ArtifactReference, "bound candidate")
    if (checked.metadata.identity != binding.identity
            or binding.identity.role != AgentRole.quality_critic
            or checked.content_format != binding.source_format
            or checked.metadata.original_request != binding.original_request
            or fingerprint(binding.original_request.model_dump(mode="json")) != binding._request_fingerprint
            or fingerprint(checked.model_dump(mode="json")) != binding._agent_input_fingerprint
            or fingerprint(checked.content) != binding.content_fingerprint):
        raise InputError("A4 original requirements or input changed after the audit was built")
    if (not isinstance(binding.authority, str) or not binding.authority.strip()
            or candidate.run_id != binding.identity.run_id or candidate.purpose != ArtifactPurpose.candidate
            or _dependencies(binding.version_dependencies) != _dependencies(checked.metadata.version_dependencies)
            or (candidate.artifact_id, candidate.revision) not in _dependencies(binding.version_dependencies)
            or fingerprint(_source_value(candidate, binding.version_dependencies, binding.authority)) != binding._source_fingerprint):
        raise InputError("A4 candidate or source snapshot changed after the audit was built")
    payload = binding.audit.model_dump(mode="python")
    payload["image"]["data"] = binding.audit.image.data
    try:
        audit = AuditInputV1.model_validate(payload)
    except (ValidationError, ValueError, TypeError) as error:
        raise InputError("bound A4 audit was mutated") from error
    if input_fingerprint(audit) != binding.input_fingerprint:
        raise InputError("bound A4 audit changed after it was built")
    constraints = _constraints(binding.original_request)
    keywords = tuple(Keyword(keyword_id=item.keyword_id, text=item.text)
                     for item in binding.original_request.keywords)
    if (binding.constraint_ids != tuple(item.constraint_id for item in constraints)
            or audit.user_constraints != constraints or audit.keywords != keywords
            or audit.image.sha256 != candidate.sha256):
        raise InputError("bound A4 audit does not match the original source mappings")
    return audit


def validate_binding(binding: AuditBinding, context: AgentContext,
                     mapping: FrameworkInputMapping) -> None:
    """Reject changed candidate, requirements, input or authority before delivery."""
    audit = validate_binding_snapshot(binding)
    checked = _check_input(binding.original_input, context, mapping)
    if (mapping.decode is not binding._decode or mapping.current_sources is not binding._current_sources
            or mapping.source_format != binding.source_format or mapping.candidate != binding.candidate):
        raise InputError("A4 input mapping changed after the audit was built")
    before = _read_current(context, mapping)
    _check_current(checked, mapping, before)
    image = _read_image(context, before.candidate)
    after = _read_current(context, mapping)
    _check_current(checked, mapping, after)
    if (any(fingerprint(_source_value(item.candidate, item.version_dependencies, item.authority)) !=
            binding._source_fingerprint for item in (before, after))
            or image.data != audit.image.data):
        raise InputError("A4 source binding changed before report delivery")
    # Authority/file callbacks may run application code. Check the request and
    # in-memory audit again after those calls, as well as their source snapshot.
    _check_input(binding.original_input, context, mapping)
    validate_binding_snapshot(binding)

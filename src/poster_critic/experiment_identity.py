"""Complete versioned A4 source/policy/environment identity for new experiments.

This inventory is distinct from the historical narrow software_fingerprint.
Nothing here rewrites a V1 frozen package or claims that hashes authenticate
external sources. Live verdict/call caches are not enabled by an identity.
"""

from __future__ import annotations

import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Annotated, Literal, Self, TYPE_CHECKING

from pydantic import Field, StringConstraints, field_validator, model_validator

from .contracts import Contract
from .errors import InputError
from .identity import fingerprint
from .language_profiles import LanguageProfileV1, legacy_language_profile, validate_language_profile

if TYPE_CHECKING:
    from .environment import EnvironmentSnapshotV1
    from .evidence_review import SecondaryReviewConfigV1
    from .provider_capabilities import ProviderCapabilityV1
    from .providers.chat import ProviderConfig

Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
INVENTORY_VERSION = 1
MAX_SOURCE_FILES = 512
MAX_SOURCE_FILE_BYTES = 4 * 1024 * 1024
MAX_SOURCE_BYTES = 64 * 1024 * 1024
_PREFIX = "src/poster_critic/"
_RESOURCE_FILES = frozenset(("resources/models.example.json", "resources/vision_ocr.swift"))
_PRIVATE_NAME = re.compile(r"^(?:private|secret|credentials)(?:[._-]|$)", re.IGNORECASE)
_REQUIRED = frozenset(("__init__.py", "contracts.py", "checks.py", "engine.py", "identity.py",
                       "annotation.js", "prompts/critic_v2.txt", "prompts/critic_v3.txt", "prompts/critic_v4.txt",
                       "resources/models.example.json", "resources/vision_ocr.swift"))


def _portable_path(value: str) -> None:
    path = PurePosixPath(value)
    if (not value.startswith(_PREFIX) or path.is_absolute() or "\\" in value
            or any(part in (".", "..") or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", part) is None for part in path.parts)
            or str(path) != value or not _allowed(value.removeprefix(_PREFIX))):
        raise ValueError("source inventory paths must be portable controlled A4 paths")


class SourceFileV1(Contract):
    path: str
    sha256: Digest
    size_bytes: Annotated[int, Field(ge=0, le=MAX_SOURCE_FILE_BYTES)]

    @field_validator("path")
    @classmethod
    def controlled_path(cls, value: str) -> str:
        _portable_path(value)
        return value

    @field_validator("size_bytes", mode="before")
    @classmethod
    def integer_size(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("source file sizes must be integers")
        return value


class ExperimentIdentityV1(Contract):
    schema_version: Literal[1] = 1
    kind: Literal["a4_experiment_identity"] = "a4_experiment_identity"
    inventory_version: Literal[1] = 1
    source_files: Annotated[tuple[SourceFileV1, ...], Field(min_length=1, max_length=MAX_SOURCE_FILES)]
    source_fingerprint: Digest
    prompt_version: Annotated[str, StringConstraints(pattern=r"^critic-v[1-9][0-9]*$")]
    prompt_sha256: Digest
    provider_config_sha256: Digest | None
    capability_sha256: Digest | None
    language_profile_sha256: Digest
    environment_sha256: Digest
    supplemental_policy_sha256: Digest | None
    execution_policy_sha256: Digest | None
    identity_sha256: Digest

    @field_validator("schema_version", "inventory_version", mode="before")
    @classmethod
    def integer_versions(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("experiment identity versions must be integers")
        return value

    @model_validator(mode="after")
    def content_bindings(self) -> Self:
        paths = [x.path for x in self.source_files]
        if paths != sorted(paths) or len(paths) != len(set(x.casefold() for x in paths)):
            raise ValueError("source inventory must be sorted and free of path collisions")
        if sum(x.size_bytes for x in self.source_files) > MAX_SOURCE_BYTES:
            raise ValueError("source inventory exceeds the total byte limit")
        if not _REQUIRED.issubset({x.removeprefix(_PREFIX) for x in paths}):
            raise ValueError("experiment source inventory omits required runtime or resources")
        if self.source_fingerprint != fingerprint([x.model_dump(mode="json") for x in self.source_files]):
            raise ValueError("source fingerprint does not match its complete inventory")
        prompt_path = _PREFIX + "prompts/critic_" + self.prompt_version.removeprefix("critic-") + ".txt"
        if not any(x.path == prompt_path and x.sha256 == self.prompt_sha256 for x in self.source_files):
            raise ValueError("actual prompt bytes must match the selected inventoried prompt version")
        if self.identity_sha256 != fingerprint(self.model_dump(mode="json", exclude={"identity_sha256"})):
            raise ValueError("experiment identity does not match its complete content")
        return self


def _allowed(relative: str) -> bool:
    path = PurePosixPath(relative)
    if any(part.startswith(".") or _PRIVATE_NAME.match(part) or part == "__pycache__" for part in path.parts):
        return False
    if path.suffix == ".py" and path.parts[0] not in ("prompts", "resources", "tests"):
        return True  # New behavior modules are discovered, not manually omitted.
    return (relative == "annotation.js" or relative in _RESOURCE_FILES
            or re.fullmatch(r"prompts/critic_v[1-9][0-9]*\.txt", relative) is not None)


def _read_source(path: Path) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_SOURCE_FILE_BYTES:
                raise InputError("source inventory requires bounded regular files")
            data = handle.read(MAX_SOURCE_FILE_BYTES + 1)
            after = os.fstat(handle.fileno())
        if (len(data) > MAX_SOURCE_FILE_BYTES or len(data) != before.st_size
                or (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise InputError("source files changed or exceeded their bounds while being inventoried")
        return data
    except OSError:
        raise InputError("cannot read controlled A4 source inventory") from None


def _inventory_once(root: Path) -> tuple[SourceFileV1, ...]:
    result: list[SourceFileV1] = []
    encountered: set[str] = set()
    total = 0
    def visit(directory: Path) -> None:
        nonlocal total
        try:
            entries = sorted(directory.iterdir(), key=lambda x: x.name)
        except OSError:
            raise InputError("cannot inspect controlled A4 source inventory") from None
        for path in entries:
            if path.is_symlink():
                raise InputError("source inventory must not follow symlinks")
            relative = path.relative_to(root).as_posix()
            # Python's validated cache is local generated state, not a source
            # artifact. It is never read, exported or hashed by this inventory.
            if path.name == "__pycache__" and path.is_dir():
                continue
            if path.is_dir():
                if path.name.startswith(".") or _PRIVATE_NAME.match(path.name) or path.name == "tests":
                    raise InputError("source inventory contains uncontrolled private or state directories")
                visit(path)
                continue
            if not _allowed(relative):
                raise InputError("source inventory contains uncontrolled config, state or resource files")
            key = relative.casefold()
            if key in encountered:
                raise InputError("source inventory contains case-colliding paths")
            encountered.add(key)
            data = _read_source(path)
            if relative == "resources/models.example.json":
                # A public configuration template must retain its typed
                # whitelist; private credential fields are never inventoried.
                from .providers.chat import ModelCatalog, ProviderConfig
                try:
                    def pairs(values):
                        result = {}
                        for key, value in values:
                            if key in result:
                                raise ValueError
                            result[key] = value
                        return result
                    packet = json.loads(data, object_pairs_hook=pairs)
                    selected = ModelCatalog if packet.get("schema_version") == 2 else ProviderConfig
                    selected.model_validate_json(data)
                except (ValueError, TypeError, AttributeError):
                    raise InputError("source template contains unsupported or private configuration") from None
            total += len(data)
            if len(result) >= MAX_SOURCE_FILES or total > MAX_SOURCE_BYTES:
                raise InputError("source inventory exceeds its fixed file or byte limit")
            try:
                result.append(SourceFileV1(path=_PREFIX + relative, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data)))
            except ValueError:
                raise InputError("source inventory contains nonportable paths") from None
    visit(root)
    paths = {x.path.removeprefix(_PREFIX) for x in result}
    if not _REQUIRED.issubset(paths):
        raise InputError("source inventory omits required A4 runtime, prompts or native resources")
    return tuple(sorted(result, key=lambda x: x.path))


def source_inventory(source_root: str | Path | None = None) -> tuple[SourceFileV1, ...]:
    """Inventory exactly an A4 package root, never a repository recursively."""
    root = Path(source_root) if source_root is not None else Path(str(files("poster_critic")))
    root = root.expanduser().absolute()
    if root.name != "poster_critic" or not root.is_dir() or any(p.is_symlink() for p in (root, *root.parents)):
        raise InputError("source root must be the regular poster_critic package directory")
    first = _inventory_once(root)
    if first != _inventory_once(root):
        raise InputError("A4 source inventory changed during collection")
    return first


def provider_config_fingerprint(config: "ProviderConfig") -> str:
    """Only the existing typed configuration whitelist, never key values."""
    from .providers.chat import ProviderConfig
    if not isinstance(config, ProviderConfig):
        raise InputError("experiment provider identity requires typed ProviderConfig")
    try:
        if set(config.__dict__) != set(ProviderConfig.model_fields):
            raise ValueError
        raw = config.model_dump(mode="python", warnings=False)
        if type(raw["schema_version"]) is not int:
            raise ValueError
        checked = ProviderConfig.model_validate(raw)
        return fingerprint(checked.model_dump(mode="json"))
    except (ValueError, TypeError, AttributeError):
        raise InputError("invalid safe provider configuration identity") from None


def supplemental_policy_fingerprint(policy: "SecondaryReviewConfigV1") -> str:
    from .evidence_review import SecondaryReviewConfigV1
    if not isinstance(policy, SecondaryReviewConfigV1):
        raise InputError("supplemental identity requires typed SecondaryReviewConfigV1")
    try:
        checked = SecondaryReviewConfigV1.model_validate_json(policy.model_dump_json())
        return fingerprint(checked.model_dump(mode="json"))
    except (ValueError, TypeError, AttributeError):
        raise InputError("invalid explicit supplemental policy identity") from None


def build_experiment_identity(*, prompt_version: str, prompt_bytes: bytes,
                              provider_config: "ProviderConfig | None" = None,
                              capability: "ProviderCapabilityV1 | None" = None,
                              language_profile: LanguageProfileV1 | None = None,
                              environment: "EnvironmentSnapshotV1 | None" = None,
                              supplemental_policy: "SecondaryReviewConfigV1 | None" = None,
                              supplemental_policy_sha256: str | None = None,
                              execution_policy_sha256: str | None = None,
                              source_root: str | Path | None = None) -> ExperimentIdentityV1:
    """Bind actual source bytes and explicit typed policies for a new experiment.

    A precomputed supplemental digest is an explicit caller binding, not proof
    of its source authority. Pass a typed policy to compute it locally instead.
    No keys, environment variable values, paths or response text are exported.
    """
    if not isinstance(prompt_bytes, bytes) or not prompt_bytes:
        raise InputError("experiment identity requires actual nonempty prompt bytes")
    inventory = source_inventory(source_root)
    selected_language = validate_language_profile(language_profile or legacy_language_profile())
    from .environment import EnvironmentSnapshotV1, collect_environment
    selected_environment = environment if environment is not None else collect_environment(scope="standalone")
    if not isinstance(selected_environment, EnvironmentSnapshotV1):
        raise InputError("experiment identity requires an actual typed environment snapshot")
    try:
        selected_environment = EnvironmentSnapshotV1.model_validate_json(selected_environment.model_dump_json())
        environment_fields = selected_environment.model_dump(mode="json", exclude={"environment_sha256"})
        if selected_environment.environment_sha256 != fingerprint(environment_fields):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise InputError("invalid stable environment identity") from None
    capability_hash = None
    if capability is not None:
        from .provider_capabilities import capability_fingerprint
        capability_hash = capability_fingerprint(capability)
    if supplemental_policy is not None:
        calculated = supplemental_policy_fingerprint(supplemental_policy)
        if supplemental_policy_sha256 is not None and supplemental_policy_sha256 != calculated:
            raise InputError("supplemental policy digest differs from its actual typed content")
        supplemental_policy_sha256 = calculated
    fields = dict(schema_version=1, kind="a4_experiment_identity", inventory_version=INVENTORY_VERSION,
        source_files=[x.model_dump(mode="json") for x in inventory],
        source_fingerprint=fingerprint([x.model_dump(mode="json") for x in inventory]),
        prompt_version=prompt_version, prompt_sha256=hashlib.sha256(prompt_bytes).hexdigest(),
        provider_config_sha256=provider_config_fingerprint(provider_config) if provider_config is not None else None,
        capability_sha256=capability_hash, language_profile_sha256=selected_language.profile_sha256,
        environment_sha256=selected_environment.environment_sha256, supplemental_policy_sha256=supplemental_policy_sha256,
        execution_policy_sha256=execution_policy_sha256)
    try:
        return ExperimentIdentityV1.model_validate_json(json.dumps({**fields, "identity_sha256": fingerprint(fields)}, allow_nan=False))
    except (ValueError, TypeError, AttributeError):
        raise InputError("invalid complete experiment identity or prompt/source binding") from None


def verify_experiment_identity(identity: ExperimentIdentityV1, **current) -> ExperimentIdentityV1:
    """Recompute with the explicitly current policy/environment; never restamp."""
    if not isinstance(identity, ExperimentIdentityV1):
        raise InputError("experiment verification requires ExperimentIdentityV1")
    try:
        identity = ExperimentIdentityV1.model_validate_json(identity.model_dump_json())
    except (ValueError, TypeError, AttributeError):
        raise InputError("invalid recorded experiment identity") from None
    actual = build_experiment_identity(**current)
    if identity != actual:
        raise InputError("experiment source, prompt, policy or environment drift detected; live calls cannot be reused")
    return actual

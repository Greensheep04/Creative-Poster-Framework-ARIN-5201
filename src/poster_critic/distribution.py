"""A4 source snapshots, bounded ZIP verification and cold offline software smoke.

No dependency installation, package-metadata replacement, model requests or Git
mutation is performed. Content seals verify integrity, not publisher identity.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import hashlib
from io import BytesIO, StringIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Annotated, Literal
from uuid import uuid4
import zipfile

from pydantic import Field, StringConstraints, field_validator, model_validator

from .contracts import Contract, Text
from .environment import CapabilityCheckV1, Digest
from .errors import InputError
from .identity import fingerprint
from .reporting import write_bytes, write_json

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILES = 512
ALLOWED_DOCUMENTS = frozenset(('A4_DEVELOPMENT_REQUIREMENTS.md', 'A4_FRAMEWORK_INTEGRATION.md',
    'A4_P1_LOCAL_TOOLS.md', 'A4_P2_DEVELOPMENT_REQUIREMENTS.md', 'QUALITY_CRITIC_QUICKSTART.md',
    'QUALITY_CRITIC_TEAM_CONTRACT.md', 'A4_P1_EVIDENCE_AND_QUALITY.md', 'A4_P2_IMPLEMENTATION.md'))
FileRole = Literal['python', 'prompt', 'javascript', 'template', 'native_source', 'document']
_SMOKE_REQUIRED = frozenset(('cold_import_origin', 'fake_three_states', 'fake_pass_test_only',
    'controlled_a1_callbacks', 'structured_diagnostics', 'crop_binding_transform', 'delivery_dedup',
    'delivery_uncertain_recovery', 'frozen_verify_replay', 'network_and_paid_calls_blocked',
    'environment_pydantic_function', 'environment_pillow_function', 'environment_a4_resources',
    'environment_posix_lock', 'environment_hardlink_atomic_create', 'environment_sqlite_local_write',
    'environment_runtime_target'))


def _relative(value):
    p = PurePosixPath(value)
    if (type(value) is not str or not value or p.is_absolute() or p.as_posix() != value
            or '\\' in value or ':' in value or '\x00' in value
            or any(part in ('', '.', '..') for part in value.split('/'))):
        raise InputError('source bundle path is not a contained portable relative path')
    return value


def _role(path):
    _relative(path)
    if path.startswith('docs/') and path.count('/') == 1 and path.split('/')[1] in ALLOWED_DOCUMENTS:
        return 'document'
    prefix = 'src/poster_critic/'
    if not path.startswith(prefix):
        raise InputError('source bundle contains a file outside the A4 allowlist')
    name = path[len(prefix):]
    if any(part.startswith('.') or part in ('tests', 'evaluation', 'fixtures', 'backup', 'backups', 'cache', 'state', '__pycache__')
           or re.match(r'(?i)^(private|secret|credentials)([._-]|$)', part)
           or re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', part) is None for part in name.split('/')):
        raise InputError('source bundle contains a private/cache path')
    if name.endswith('.py'):
        return 'python'
    if name == 'annotation.js':
        return 'javascript'
    if name in ('prompts/critic_v1.txt', 'prompts/critic_v2.txt', 'prompts/critic_v3.txt', 'prompts/critic_v4.txt'):
        return 'prompt'
    if name == 'resources/models.example.json':
        return 'template'
    if name == 'resources/vision_ocr.swift':
        return 'native_source'
    raise InputError('source bundle contains an undeclared A4 resource')


class VersionedContract(Contract):
    schema_version: Literal[1] = 1

    @field_validator('schema_version', mode='before')
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError('source delivery version must be an integer')
        return value


class SourceBundleFileV1(Contract):
    path: Text
    role: FileRole
    size_bytes: int = Field(ge=0, le=MAX_FILE_BYTES)
    sha256: Digest

    @model_validator(mode='after')
    def allowed(self):
        if _role(self.path) != self.role:
            raise ValueError('source file role differs from its allowlisted path')
        return self


class SourceBundleManifestV1(VersionedContract):
    kind: Literal['a4_source_bundle'] = 'a4_source_bundle'
    scope: Literal['standalone'] = 'standalone'
    bundle_id: Text
    a4_source_version: Text
    source_revision: Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{40,64}$')] | None
    working_tree: bool
    files: tuple[SourceBundleFileV1, ...] = Field(min_length=1, max_length=MAX_FILES - 1)
    inventory_sha256: Digest
    manifest_sha256: Digest

    @model_validator(mode='after')
    def valid_inventory(self):
        names = [x.path for x in self.files]
        if names != sorted(set(names)) or len({x.casefold() for x in names}) != len(names):
            raise ValueError('source inventory must be sorted and have no duplicate/case collisions')
        if sum(x.size_bytes for x in self.files) > MAX_TOTAL_BYTES:
            raise ValueError('source inventory exceeds the total limit')
        if fingerprint([x.model_dump(mode='json') for x in self.files]) != self.inventory_sha256:
            raise ValueError('source inventory hash differs')
        if fingerprint(self.model_dump(mode='json', exclude={'manifest_sha256'})) != self.manifest_sha256:
            raise ValueError('source manifest hash differs')
        return self


class ArchiveReceiptV1(VersionedContract):
    kind: Literal['a4_archive_receipt'] = 'a4_archive_receipt'
    archive_name: Text
    archive_sha256: Digest
    manifest_sha256: Digest
    inventory_sha256: Digest
    size_bytes: int = Field(ge=1, le=MAX_ARCHIVE_BYTES)

    @field_validator('archive_name')
    @classmethod
    def plain_name(cls, value):
        if '/' in _relative(value):
            raise ValueError('archive receipt uses a plain portable filename')
        return value


class BundleVerificationV1(VersionedContract):
    kind: Literal['a4_bundle_verification'] = 'a4_bundle_verification'
    archive_sha256: Digest | None
    manifest_sha256: Digest | None
    inventory_sha256: Digest | None
    status: Literal['verified', 'rejected']
    checks: tuple[CapabilityCheckV1, ...]

    @model_validator(mode='after')
    def complete_verification(self):
        if self.status == 'verified' and (any(value is None for value in (
                self.archive_sha256, self.manifest_sha256, self.inventory_sha256))
                or not self.checks or any(c.status != 'passed' for c in self.checks)):
            raise ValueError('verified bundle requires all real hashes and complete checks')
        return self


class OfflineSmokeReceiptV1(VersionedContract):
    kind: Literal['a4_offline_smoke'] = 'a4_offline_smoke'
    inventory_sha256: Digest
    environment_sha256: Digest | None
    scope: Literal['standalone', 'framework_adapter']
    checks: tuple[CapabilityCheckV1, ...]
    controlled_release_callbacks: int = Field(ge=0)
    controlled_a1_callbacks: int = Field(ge=0)
    network_requests: Literal[0] = 0
    paid_model_calls: Literal[0] = 0
    quality_claim: Literal['software_behavior_only'] = 'software_behavior_only'
    status: Literal['passed', 'failed', 'blocked']

    @field_validator('network_requests', 'paid_model_calls', mode='before')
    @classmethod
    def exact_zero_count(cls, value):
        if type(value) is not int:
            raise ValueError('smoke call counts must be integers')
        return value

    @model_validator(mode='after')
    def complete_smoke(self):
        names = [c.check_id for c in self.checks]
        if len(names) != len(set(names)):
            raise ValueError('smoke checks must be unique')
        if self.status == 'passed' and (self.environment_sha256 is None or not self.checks
                or any(c.status not in ('passed', 'not_assessed') for c in self.checks)
                or self.controlled_release_callbacks != 0):
            raise ValueError('passed smoke requires actual complete evidence and no fake release')
        if self.status == 'passed':
            if 'relocation_equivalence' in names:
                required = {f'cold_{number}_{name}' for number in (1, 2) for name in _SMOKE_REQUIRED}
                if self.controlled_a1_callbacks != 10:
                    raise ValueError('passed relocation smoke must retain both controlled callback counts')
            else:
                required = _SMOKE_REQUIRED
                if self.controlled_a1_callbacks != 5:
                    raise ValueError('passed child smoke must retain actual controlled callback counts')
            passed = {c.check_id for c in self.checks if c.status == 'passed'}
            if not required.issubset(passed):
                raise ValueError('passed smoke omitted a required behavior/capability check')
        return self


def _json(raw, model):
    try:
        def pairs(items):
            result = {}
            for k, v in items:
                if k in result:
                    raise ValueError('duplicate JSON field')
                result[k] = v
            return result
        json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        return model.model_validate_json(raw)
    except (ValueError, UnicodeError, TypeError):
        raise InputError('source delivery object is invalid') from None


def _regular(path, limit):
    path = Path(path).expanduser().absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise InputError('source delivery paths must not contain symlinks')
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise InputError('source delivery input is not a bounded regular file')
        with os.fdopen(fd, 'rb') as file:
            fd = None
            result = bytearray()
            while True:
                part = file.read(min(64 * 1024, limit - len(result) + 1))
                if not part:
                    break
                result.extend(part)
                if len(result) > limit:
                    raise InputError('source delivery input exceeded its actual byte limit')
            return bytes(result)
    finally:
        if fd is not None:
            os.close(fd)


def _new_directory(path):
    value = Path(path).expanduser().absolute()
    if value.exists() or any(p.is_symlink() for p in (value, *value.parents)):
        raise InputError('source delivery destination must be new without symlinks')
    return value


def _receipt(value):
    if isinstance(value, ArchiveReceiptV1):
        return _json(value.model_dump_json(), ArchiveReceiptV1)
    return _json(_regular(value, MAX_FILE_BYTES), ArchiveReceiptV1)


def _zip_contents(raw):
    contents, total = {}, 0
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        members = archive.infolist()
        if not 1 <= len(members) <= MAX_FILES:
            raise InputError('source ZIP file-count limit exceeded')
        seen = set()
        for item in members:
            name = _relative(item.filename)
            mode = item.external_attr >> 16
            if (name.casefold() in seen or item.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                    or item.flag_bits & 1 or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or item.file_size > MAX_FILE_BYTES or item.file_size < 0):
                raise InputError('source ZIP has a duplicate, unsafe member or unsupported encoding')
            seen.add(name.casefold())
            total += item.file_size
            if total > MAX_TOTAL_BYTES:
                raise InputError('source ZIP declared total exceeds the limit')
        total = 0
        for item in members:
            with archive.open(item) as member:
                data = bytearray()
                while True:
                    block = member.read(64 * 1024)
                    if not block:
                        break
                    data.extend(block); total += len(block)
                    if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                        raise InputError('source ZIP actual expansion exceeded its byte limit')
                if len(data) != item.file_size:
                    raise InputError('source ZIP actual length differs from its header')
                contents[item.filename] = bytes(data)
    return contents


def _validate_contents(contents):
    if 'manifest.json' not in contents:
        raise InputError('source manifest is missing')
    manifest = _json(contents['manifest.json'], SourceBundleManifestV1)
    inventory = {item.path: item for item in manifest.files}
    if set(contents) != set(inventory) | {'manifest.json'}:
        raise InputError('source bundle includes missing or undeclared files')
    if sum(len(raw) for raw in contents.values()) > MAX_TOTAL_BYTES:
        raise InputError('source bundle actual total exceeds the limit')
    for name, item in inventory.items():
        raw = contents[name]
        if len(raw) != item.size_bytes or hashlib.sha256(raw).hexdigest() != item.sha256:
            raise InputError('source bundle file hash or size differs')
        if item.role == 'template':
            from .providers.chat import ModelCatalog
            _json(raw, ModelCatalog)
    required = {'src/poster_critic/__init__.py', 'src/poster_critic/engine.py', 'src/poster_critic/contracts.py',
                'src/poster_critic/distribution.py', 'src/poster_critic/environment.py',
                'src/poster_critic/prompts/critic_v2.txt', 'src/poster_critic/prompts/critic_v3.txt',
                'src/poster_critic/prompts/critic_v4.txt', 'src/poster_critic/annotation.js',
                'src/poster_critic/resources/models.example.json', 'src/poster_critic/resources/vision_ocr.swift'}
    if not required.issubset(inventory):
        raise InputError('source bundle omits mandatory A4 behavior/resources')
    return manifest


def build_source_bundle(package_root, out, *, docs=(), bundle_id=None, a4_source_version=None,
                         source_revision=None, working_tree=True, include_legacy_prompt=False) -> SourceBundleManifestV1:
    """Capture only A4 inventory into a new ZIP/receipt/verified snapshot family."""
    package = Path(package_root).expanduser().absolute()
    if package.name != 'poster_critic' or not package.is_dir() or any(p.is_symlink() for p in (package, *package.parents)):
        raise InputError('package_root must be the actual A4 package directory, never a repository root')
    if type(working_tree) is not bool or type(include_legacy_prompt) is not bool:
        raise InputError('working-tree and legacy-prompt selections must be explicit booleans')
    from .experiment_identity import source_inventory
    inventory = source_inventory(package)
    contents = {}
    for item in inventory:
        name = item.path
        if name == 'src/poster_critic/prompts/critic_v1.txt' and not include_legacy_prompt:
            continue
        _role(name)
        relative = name.removeprefix('src/poster_critic/')
        raw = _regular(package / relative, MAX_FILE_BYTES)
        if hashlib.sha256(raw).hexdigest() != item.sha256 or len(raw) != item.size_bytes:
            raise InputError('source changed while capturing the bundle')
        contents[name] = raw
    for document in docs:
        path = Path(document).expanduser().absolute()
        if path.parent.name != 'docs' or path.name not in ALLOWED_DOCUMENTS:
            raise InputError('only explicitly selected A4 documents may enter the source bundle')
        name = 'docs/' + path.name
        if name in contents:
            raise InputError('duplicate document selection')
        contents[name] = _regular(path, MAX_FILE_BYTES)
    if len(contents) + 1 > MAX_FILES or sum(len(v) for v in contents.values()) > MAX_TOTAL_BYTES:
        raise InputError('source bundle inventory exceeds fixed limits')
    entries = [dict(path=name, role=_role(name), size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
               for name, raw in sorted(contents.items())]
    from . import __version__
    body = dict(schema_version=1, kind='a4_source_bundle', scope='standalone',
        bundle_id=bundle_id or 'a4-source-' + uuid4().hex, a4_source_version=a4_source_version or 'source-' + __version__,
        source_revision=source_revision, working_tree=working_tree, files=entries, inventory_sha256=fingerprint(entries))
    manifest = _json(json.dumps(dict(body, manifest_sha256=fingerprint(body))), SourceBundleManifestV1)
    contents['manifest.json'] = (manifest.model_dump_json(indent=2) + '\n').encode()
    _validate_contents(contents)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(contents.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, raw)
    raw = buffer.getvalue()
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise InputError('source ZIP archive exceeds fixed byte limit')
    destination = _new_directory(out)
    if destination.is_relative_to(package):
        raise InputError('source output must be outside the A4 package')
    destination.mkdir(parents=True)
    write_json(destination / '.incomplete.json', {'kind': 'a4_incomplete_source_delivery'})
    archive_path = destination / 'a4-source.zip'
    write_bytes(archive_path, raw)
    receipt = ArchiveReceiptV1(archive_name=archive_path.name, archive_sha256=hashlib.sha256(raw).hexdigest(),
        manifest_sha256=manifest.manifest_sha256, inventory_sha256=manifest.inventory_sha256, size_bytes=len(raw))
    write_json(destination / 'archive_receipt.json', receipt.model_dump(mode='json'))
    extract_source_bundle(archive_path, receipt, destination / 'snapshot')
    (destination / '.incomplete.json').unlink()
    return manifest


def verify_source_bundle(bundle, receipt) -> BundleVerificationV1:
    archive_hash = manifest_hash = inventory_hash = None
    try:
        raw = _regular(bundle, MAX_ARCHIVE_BYTES)
        archive_hash = hashlib.sha256(raw).hexdigest()
        received = _receipt(receipt)
        if (Path(bundle).name != received.archive_name or archive_hash != received.archive_sha256
                or len(raw) != received.size_bytes):
            raise InputError('archive receipt differs')
        manifest = _validate_contents(_zip_contents(raw))
        manifest_hash, inventory_hash = manifest.manifest_sha256, manifest.inventory_sha256
        if manifest_hash != received.manifest_sha256 or inventory_hash != received.inventory_sha256:
            raise InputError('manifest receipt differs')
        check = CapabilityCheckV1(check_id='archive_and_inventory', status='passed', reason_code='all_bytes_paths_roles_and_limits_verified')
        status = 'verified'
    except (InputError, OSError, ValueError, RuntimeError, zipfile.BadZipFile, EOFError):
        check = CapabilityCheckV1(check_id='archive_and_inventory', status='failed', reason_code='archive_or_manifest_rejected')
        status = 'rejected'
    return BundleVerificationV1(archive_sha256=archive_hash, manifest_sha256=manifest_hash,
                                inventory_sha256=inventory_hash, status=status, checks=(check,))


def extract_source_bundle(bundle, receipt, out) -> SourceBundleManifestV1:
    destination = _new_directory(out)
    verified = verify_source_bundle(bundle, receipt)
    if verified.status != 'verified':
        raise InputError('source bundle rejected before extraction')
    raw = _regular(bundle, MAX_ARCHIVE_BYTES)
    if hashlib.sha256(raw).hexdigest() != verified.archive_sha256:
        raise InputError('source ZIP changed before extraction')
    contents = _zip_contents(raw)
    manifest = _validate_contents(contents)
    destination.mkdir(parents=True)
    write_json(destination / '.incomplete.json', {'kind': 'a4_incomplete_source_extraction'})
    for name, value in sorted(contents.items()):
        write_bytes(destination / name, value)
    (destination / '.incomplete.json').unlink()
    verify_source_snapshot(destination)
    return manifest


def verify_source_snapshot(root) -> SourceBundleManifestV1:
    root = Path(root).expanduser().absolute()
    if not root.is_dir() or any(p.is_symlink() for p in (root, *root.parents)):
        raise InputError('source snapshot requires a regular directory without symlinks')
    contents = {}
    for path in root.rglob('*'):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise InputError('source snapshot contains a non-regular member')
        if path.is_file():
            name = path.relative_to(root).as_posix()
            _relative(name)
            contents[name] = _regular(path, MAX_FILE_BYTES)
            if len(contents) > MAX_FILES or sum(len(v) for v in contents.values()) > MAX_TOTAL_BYTES:
                raise InputError('source snapshot limits exceeded')
    return _validate_contents(contents)


def _child_smoke(root, out, *, scope, expected_framework_revision=None):
    """Called by the guarded cold interpreter, never by a production flow."""
    from .environment import collect_environment
    from .adapters.local_input import load_input
    from .contracts import AuditReportV3
    from .delivery import (DeliveryAlreadyHandledError, DeliveryGuard, DeliveryUncertainError, delivery_key)
    from .dispatch import audit_and_dispatch
    from .engine import AuditEngine
    from .evidence import (canvas_to_crop_bbox, crop_evidence, crop_to_canvas_bbox, validate_crop_evidence)
    from .frozen_evaluation import freeze_evaluation, replay_frozen_evaluation, verify_frozen_evaluation
    from .identity import input_fingerprint
    from .providers.fake import FakeProvider
    from .runtime import StandaloneExecutor
    from .structured_diagnostics import structured_diagnostics
    from PIL import Image
    root, out = Path(root), Path(out)
    manifest = verify_source_snapshot(root)
    checks, counters, signatures = [], {'release': 0, 'a1': 0}, {}
    environment = collect_environment(scope=scope, expected_framework_revision=expected_framework_revision, source_bundle_root=root)
    checks.extend(CapabilityCheckV1(check_id='environment_' + c.check_id, status=c.status, reason_code=c.reason_code)
                  for c in environment.capabilities)
    def passed(name):
        checks.append(CapabilityCheckV1(check_id=name, status='passed', reason_code='actual_controlled_check_passed'))
    status = 'blocked' if any(c.status == 'blocked' for c in checks) else 'failed' if any(c.status == 'failed' for c in checks) else 'passed'
    if status == 'passed':
        import socket
        from .providers.chat import ChatProvider
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except RuntimeError as exc:
            if str(exc) != 'offline_network_socket_blocked':
                raise
        else:
            probe.close()
            raise InputError('cold smoke network guard was not installed')
        try:
            asyncio.run(ChatProvider.invoke(None, None, ''))
        except RuntimeError as exc:
            if str(exc) != 'paid_model_calls_blocked':
                raise
        else:
            raise InputError('cold smoke paid-call guard was not installed')
        passed('network_and_paid_calls_blocked')
        # Verify every loaded A4 module originates from the captured source.
        for name, module in tuple(sys.modules.items()):
            if name == 'poster_critic' or name.startswith('poster_critic.'):
                origin = getattr(module, '__file__', None)
                if origin is not None and not Path(origin).resolve().is_relative_to((root / 'src/poster_critic').resolve()):
                    raise InputError('cold smoke imported an A4 module from outside its snapshot')
        passed('cold_import_origin')
        work = out / 'generated'
        work.mkdir(parents=True, exist_ok=False)
        rows = []
        for name in ('pass', 'fail', 'unknown'):
            Image.new('RGB', (720, 1280), (240, 240, 240)).save(work / (name + '.png'))
            write_json(work / (name + '.json'), {'schema_version': 1, 'sample_id': name, 'image': {'path': name + '.png'},
                'keywords': [{'keyword_id': 'k1', 'text': 'coffee'}, {'keyword_id': 'k2', 'text': 'plants'}],
                'user_constraints': [], 'approved_copy': [{'copy_id': 'title', 'text': 'DEMO', 'required': True}],
                'required_checks': ['keyword_alignment', 'user_constraints', 'text_readability', 'layout_quality'],
                'design_context': None, 'layout_context': None, 'asset_context': None, 'render_context': None})
            response = {'keyword_checks': [{'keyword_id': 'k1', 'status': 'fail' if name == 'fail' else 'pass', 'evidence': 'Controlled fixed software observation.'},
                {'keyword_id': 'k2', 'status': 'pass', 'evidence': 'Controlled fixed software observation.'}],
                'constraint_checks': [], 'text_checks': [{'copy_id': 'title', 'observed_text': None if name == 'unknown' else 'DEMO',
                    'status': 'unknown' if name == 'unknown' else 'pass', 'readability': 'unknown' if name == 'unknown' else 'pass'}],
                'visual_checks': [{'check_id': 'text_readability', 'status': 'pass', 'evidence': 'Controlled fixed software observation.'},
                    {'check_id': 'layout_quality', 'status': 'pass', 'evidence': 'Controlled fixed software observation.'}],
                'issues': [], 'uncertainties': []}
            write_json(work / (name + '.observation.json'), response)
            rows.append({'sample_id': name, 'input_path': name + '.json', 'split': 'dev', 'source_group': 'synthetic-software',
                'source': 'Generated software control; no human or model-quality claim.', 'expected_decision': name,
                'expected_issue_codes': ['keyword_missing'] if name == 'fail' else [], 'allowed_repair_stages': [],
                'response_path': name + '.observation.json'})
        write_json(work / 'dataset.json', {'schema_version': 1, 'purpose': 'software_demo', 'samples': rows})
        async def suite():
            async def release(result):
                counters['release'] += 1
            async def feedback(result):
                counters['a1'] += 1
            results = {}
            for name in ('pass', 'fail', 'unknown'):
                audit = load_input(work / (name + '.json'))
                result = await audit_and_dispatch(audit, AuditEngine(FakeProvider.from_file(work / (name + '.observation.json')),
                    StandaloneExecutor()), on_release=release, on_feedback=feedback)
                if result.report.decision != name or result.route != ('test_only' if name == 'pass' else 'a1'):
                    raise InputError('cold smoke three-state dispatch differs')
                envelope = structured_diagnostics(audit, result.report)
                if envelope.input_fingerprint != input_fingerprint(audit) or envelope.decision != name:
                    raise InputError('cold smoke diagnostic binding differs')
                crop = crop_evidence(audit, (100, 200, 300, 400), output_size=(100, 100))
                validate_crop_evidence(crop, audit)
                box = (0.0, 0.0, 100.0, 100.0)
                if canvas_to_crop_bbox(crop, crop_to_canvas_bbox(crop, box)) != box:
                    raise InputError('cold smoke crop transform differs')
                signatures[name] = {'input_fingerprint': input_fingerprint(audit), 'image_sha256': audit.image.sha256,
                    'decision': result.report.decision, 'issues': sorted(x.code for x in result.report.issues), 'route': result.route}
                results[name] = result
            if counters != {'release': 0, 'a1': 2}:
                raise InputError('cold smoke callback counts differ')
            for name in ('fake_three_states', 'fake_pass_test_only', 'controlled_a1_callbacks', 'structured_diagnostics', 'crop_binding_transform'):
                passed(name)
            ledger = DeliveryGuard(out / 'delivery.sqlite', handler_timeout=1.0)
            pending = results['fail'].model_copy(update={'handler_status': 'pending'})
            await ledger.deliver(pending, feedback, candidate_identity='dedup')
            try:
                await ledger.deliver(pending, feedback, candidate_identity='dedup')
            except DeliveryAlreadyHandledError:
                passed('delivery_dedup')
            else:
                raise InputError('cold smoke duplicated a callback')
            async def failure(result):
                counters['a1'] += 1
                raise RuntimeError('controlled failure before simulated side effect')
            try:
                await ledger.deliver(pending, failure, candidate_identity='uncertain')
            except RuntimeError:
                pass
            key = delivery_key(pending, candidate_identity='uncertain')
            if ledger.lookup(key).state != 'uncertain':
                raise InputError('cold smoke lost uncertain delivery')
            try:
                await ledger.deliver(pending, feedback, candidate_identity='uncertain')
            except DeliveryUncertainError:
                pass
            else:
                raise InputError('cold smoke replayed uncertain delivery')
            await asyncio.sleep(0)
            ledger.reconcile(key, outcome='confirmed_not_applied', caller_confirmed=True,
                evidence='Controlled callback raised before its local simulated side effect.')
            if (await ledger.deliver(pending, feedback, candidate_identity='uncertain')).state != 'completed':
                raise InputError('cold smoke controlled recovery did not complete')
            passed('delivery_uncertain_recovery')
            freeze_evaluation(work / 'dataset.json', out / 'frozen')
            verify_frozen_evaluation(out / 'frozen')
            summary = await replay_frozen_evaluation(out / 'frozen', out / 'replay', repeats=2)
            if summary['planned_audits'] != 6 or summary['decision_agreement']['numerator'] != 6 or summary['network_requests'] != 0:
                raise InputError('cold smoke fixed replay differs')
            passed('frozen_verify_replay')
        asyncio.run(suite())
        for name, module in tuple(sys.modules.items()):
            if name == 'poster_critic' or name.startswith('poster_critic.'):
                origin = getattr(module, '__file__', None)
                if origin is not None and not Path(origin).resolve().is_relative_to((root / 'src/poster_critic').resolve()):
                    raise InputError('later cold import escaped the A4 snapshot')
    receipt = OfflineSmokeReceiptV1(inventory_sha256=manifest.inventory_sha256,
        environment_sha256=environment.environment_sha256, scope=scope, checks=tuple(checks),
        controlled_release_callbacks=counters['release'], controlled_a1_callbacks=counters['a1'], status=status)
    write_json(out / 'child_receipt.json', receipt.model_dump(mode='json'))
    write_json(out / 'smoke_observations.json', signatures)


_BOOTSTRAP = r'''
import json, os, pathlib, socket, sys
sys.dont_write_bytecode = True
root, out, scope, revision = sys.argv[1:5]
sys.path.insert(0, str(pathlib.Path(root) / 'src'))
def guard(event, args):
    if event.startswith(('subprocess.', 'os.system', 'os.exec', 'os.posix_spawn', 'ctypes.')):
        raise RuntimeError('offline_process_or_native_escape_blocked')
    if event == 'socket.__new__' and len(args) > 1 and args[1] not in (socket.AF_UNIX,):
        raise RuntimeError('offline_network_socket_blocked')
    if event in ('socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr'):
        raise RuntimeError('offline_dns_blocked')
    if event in ('socket.connect', 'socket.bind') and len(args) > 1 and isinstance(args[1], tuple):
        raise RuntimeError('offline_network_connection_blocked')
sys.addaudithook(guard)
try:
    import poster_critic.providers.chat as chat
    async def no_model(*args, **kwargs):
        raise RuntimeError('paid_model_calls_blocked')
    chat.ChatProvider.invoke = no_model
    from poster_critic.distribution import _child_smoke
    _child_smoke(root, out, scope=scope, expected_framework_revision=revision or None)
except (ImportError, ModuleNotFoundError):
    pathlib.Path(out, 'cold_error.json').write_text(json.dumps({'status':'blocked','reason_code':'cold_dependency_missing'}))
except BaseException:
    pathlib.Path(out, 'cold_error.json').write_text(json.dumps({'status':'failed','reason_code':'cold_smoke_behavior_failed'}))
'''


def run_offline_smoke(bundle_root, out, *, scope='standalone', expected_framework_revision=None) -> OfflineSmokeReceiptV1:
    """Verify, copy outside the repository, run two guarded cold interpreters."""
    if scope not in ('standalone', 'framework_adapter'):
        raise InputError('choose the declared smoke scope')
    root = Path(bundle_root).expanduser().absolute()
    manifest = verify_source_snapshot(root)
    destination = _new_directory(out)
    if destination.is_relative_to(root) or root.is_relative_to(destination):
        raise InputError('smoke output must be separate from its source snapshot')
    destination.mkdir(parents=True)
    checks, children = [], []
    with tempfile.TemporaryDirectory(prefix='a4-cold-source-') as temp:
        cold = Path(temp).resolve()
        for index in (1, 2):
            location = cold / ('original' if index == 1 else 'moved')
            if index == 1:
                shutil.copytree(root, location)
            else:
                shutil.move(cold / 'original', location)
            verify_source_snapshot(location)
            run_out = destination / f'cold-{index}'
            run_out.mkdir()
            try:
                subprocess.run([sys.executable, '-I', '-c', _BOOTSTRAP, str(location), str(run_out), scope,
                                expected_framework_revision or ''], cwd=cold, timeout=60,
                               env={'PATH': os.defpath, 'LC_ALL': 'C'}, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, check=True)
            except (OSError, subprocess.SubprocessError):
                children.append(None)
                checks.append(CapabilityCheckV1(check_id=f'cold_{index}', status='failed', reason_code='cold_process_failed'))
                continue
            verify_source_snapshot(location)
            if (run_out / 'cold_error.json').exists():
                value = json.loads(_regular(run_out / 'cold_error.json', MAX_FILE_BYTES))
                children.append(None)
                checks.append(CapabilityCheckV1(check_id=f'cold_{index}', status=value['status'], reason_code=value['reason_code']))
            else:
                child = _json(_regular(run_out / 'child_receipt.json', MAX_FILE_BYTES), OfflineSmokeReceiptV1)
                if child.inventory_sha256 != manifest.inventory_sha256 or child.scope != scope:
                    raise InputError('cold receipt identity differs')
                children.append(child)
                checks.extend(CapabilityCheckV1(check_id=f'cold_{index}_{c.check_id}', status=c.status, reason_code=c.reason_code)
                              for c in child.checks)
        if all(children) and all(child.status == 'passed' for child in children):
            left = json.loads(_regular(destination / 'cold-1/smoke_observations.json', MAX_FILE_BYTES))
            right = json.loads(_regular(destination / 'cold-2/smoke_observations.json', MAX_FILE_BYTES))
            same = bool(left) and left == right
            checks.append(CapabilityCheckV1(check_id='relocation_equivalence', status='passed' if same else 'failed',
                                           reason_code='input_decisions_issues_routes_equal' if same else 'relocation_mismatch'))
        else:
            checks.append(CapabilityCheckV1(check_id='relocation_equivalence', status='blocked', reason_code='cold_run_not_complete'))
    verify_source_snapshot(root)
    status = 'failed' if any(c.status == 'failed' for c in checks) else 'blocked' if any(c.status == 'blocked' for c in checks) else 'passed'
    receipt = OfflineSmokeReceiptV1(inventory_sha256=manifest.inventory_sha256,
        environment_sha256=children[0].environment_sha256 if children[0] is not None else None, scope=scope,
        checks=tuple(checks), controlled_release_callbacks=sum(c.controlled_release_callbacks for c in children if c),
        controlled_a1_callbacks=sum(c.controlled_a1_callbacks for c in children if c), status=status)
    write_json(destination / 'smoke_receipt.json', receipt.model_dump(mode='json'))
    return receipt


class WheelMetadataPolicyV1(VersionedContract):
    """Explicit expected metadata. Its contents do not authenticate approval."""
    kind: Literal['a4_wheel_metadata_policy'] = 'a4_wheel_metadata_policy'
    name: Text
    version: Text
    requires_python: Text
    requires_dist: tuple[Text, ...]
    provides_extra: tuple[Text, ...]


class WheelVerificationV1(VersionedContract):
    kind: Literal['a4_wheel_verification'] = 'a4_wheel_verification'
    status: Literal['verified', 'rejected']
    wheel_sha256: Digest | None
    name: Text | None
    version: Text | None
    requires_python: Text | None
    requires_dist: tuple[Text, ...]
    checks: tuple[CapabilityCheckV1, ...]
    metadata_policy_sha256: Digest | None = None

    @model_validator(mode='after')
    def actual_verification(self):
        if self.status == 'verified' and (self.wheel_sha256 is None or self.name is None
                or self.version is None or self.requires_python is None or not self.checks
                or any(check.status != 'passed' for check in self.checks)):
            raise ValueError('verified wheel requires actual metadata, hashes and checks')
        return self


def _wheel_metadata_fields(name, version, requires_python, dependencies, extras):
    # Optional inspection dependency only. No import for ordinary A4 source
    # runs, no installation and no changes to the shared dependency manifest.
    try:
        from packaging.requirements import Requirement
        from packaging.specifiers import SpecifierSet
        from packaging.utils import canonicalize_name
        from packaging.version import Version
    except ImportError:
        raise InputError('wheel_metadata_parser_unavailable') from None
    from .response_accounting import _SECRET
    if any(_SECRET.search(value) for value in (name,version,requires_python,*dependencies,*extras)):
        raise InputError('wheel metadata contains sensitive values')
    normalized_name = canonicalize_name(name, validate=True)
    parsed_version = Version(version)
    python_spec = SpecifierSet(requires_python)
    requirements = tuple(Requirement(value) for value in dependencies)
    if any(value.url is not None for value in requirements):
        # This source inspection supports public index dependencies only. URL
        # requirements can embed credentials or private paths in the receipt.
        raise InputError('wheel direct URL dependencies are outside the supported delivery policy')
    normalized_extras = tuple(canonicalize_name(value, validate=True) for value in extras)
    if len(set(requirements)) != len(requirements) or len(set(normalized_extras)) != len(normalized_extras):
        raise InputError('wheel metadata contains duplicate normalized dependencies or extras')
    return normalized_name, parsed_version, python_spec, frozenset(requirements), frozenset(normalized_extras)


def verify_existing_wheel(wheel, *, source_bundle_root, expected_name=None, expected_version=None,
                          metadata_policy: WheelMetadataPolicyV1 | None = None) -> WheelVerificationV1:
    """Verify existing bytes/metadata/RECORD only. Never build or install a wheel."""
    digest, name, version, requires_python, dependencies, policy_hash = None, None, None, None, (), None
    try:
        from email.parser import BytesParser
        manifest = verify_source_snapshot(source_bundle_root)
        raw = _regular(wheel, MAX_ARCHIVE_BYTES)
        digest = hashlib.sha256(raw).hexdigest()
        contents = _zip_contents(raw)
        metadata_paths = [p for p in contents if p.endswith('.dist-info/METADATA')]
        if len(metadata_paths) != 1:
            raise InputError('wheel must have one metadata owner')
        directory = metadata_paths[0].removesuffix('/METADATA')
        info = BytesParser().parsebytes(contents[metadata_paths[0]])
        wheel_info = BytesParser().parsebytes(contents[directory + '/WHEEL'])
        if any(len(info.get_all(header, ())) != 1 for header in ('Metadata-Version','Name', 'Version', 'Requires-Python')):
            raise InputError('wheel singleton metadata is missing or duplicated')
        name, version, requires_python = info.get('Name'), info.get('Version'), info.get('Requires-Python')
        dependencies = tuple(info.get_all('Requires-Dist', ()))
        extras = tuple(info.get_all('Provides-Extra', ()))
        metadata_version = info.get('Metadata-Version')
        if re.fullmatch(r'[0-9]+\.[0-9]+',metadata_version or '') is None:
            raise InputError('wheel core metadata version is invalid')
        if tuple(int(x) for x in metadata_version.split('.')) >= (2,3) and any(
                re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',value) is None for value in extras):
            raise InputError('wheel extras violate the declared core metadata version')
        if (not name or not version or not requires_python or wheel_info.get('Wheel-Version') != '1.0'
                or not wheel_info.get_all('Tag') or expected_name is not None and name != expected_name
                or expected_version is not None and version != expected_version):
            raise InputError('wheel metadata differs or is incomplete')
        actual_metadata = _wheel_metadata_fields(name,version,requires_python,dependencies,extras)
        if metadata_policy is not None:
            if not isinstance(metadata_policy,WheelMetadataPolicyV1):
                raise InputError('wheel requires a typed explicit metadata policy')
            policy = WheelMetadataPolicyV1.model_validate_json(metadata_policy.model_dump_json())
            expected_metadata = _wheel_metadata_fields(policy.name,policy.version,policy.requires_python,
                                                        policy.requires_dist,policy.provides_extra)
            policy_hash = fingerprint(policy.model_dump(mode='json'))
            if actual_metadata != expected_metadata:
                raise InputError('wheel metadata differs from the complete expected policy')
        record_path = directory + '/RECORD'
        recorded = {}
        for row in csv.reader(StringIO(contents[record_path].decode('utf-8'))):
            if len(row) != 3 or row[0] in recorded:
                raise InputError('wheel RECORD is invalid')
            _relative(row[0]); recorded[row[0]] = row[1:]
        if set(recorded) != set(contents):
            raise InputError('wheel RECORD inventory differs')
        for path, (record_hash, record_size) in recorded.items():
            if path == record_path:
                if record_hash or record_size:
                    raise InputError('wheel RECORD self entry must omit its hash/size')
                continue
            value = contents[path]
            expected = 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(value).digest()).decode().rstrip('=')
            if record_hash != expected or record_size != str(len(value)):
                raise InputError('wheel RECORD content differs')
        required = {item.path.removeprefix('src/'): item for item in manifest.files if item.path.startswith('src/')}
        found = {p for p in contents if p.startswith('poster_critic/')}
        if set(required) != found or any(hashlib.sha256(contents[p]).hexdigest() != item.sha256 for p, item in required.items()):
            raise InputError('wheel A4 code/resources differ from the source snapshot')
        check, status = CapabilityCheckV1(check_id='wheel_bytes_metadata_record', status='passed',
            reason_code='content_verified_no_install_or_platform_claim'), 'verified'
    except (InputError, OSError, ValueError, TypeError, KeyError, UnicodeError, zipfile.BadZipFile):
        check, status = CapabilityCheckV1(check_id='wheel_bytes_metadata_record', status='failed', reason_code='wheel_rejected'), 'rejected'
        name,version,requires_python,dependencies = None,None,None,()
    return WheelVerificationV1(status=status, wheel_sha256=digest, name=name, version=version,
        requires_python=requires_python, requires_dist=dependencies, checks=(check,),metadata_policy_sha256=policy_hash)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    snapshot = sub.add_parser('snapshot')
    snapshot.add_argument('--package-root', type=Path, required=True)
    snapshot.add_argument('--out', type=Path, required=True)
    snapshot.add_argument('--doc', type=Path, action='append', default=[])
    snapshot.add_argument('--bundle-id')
    snapshot.add_argument('--source-version')
    snapshot.add_argument('--source-revision')
    snapshot.add_argument('--legacy-prompt', action='store_true')
    verify = sub.add_parser('verify')
    verify.add_argument('--bundle', type=Path, required=True)
    verify.add_argument('--receipt', type=Path, required=True)
    verify.add_argument('--extract', type=Path)
    smoke = sub.add_parser('smoke')
    smoke.add_argument('--bundle-root', type=Path, required=True)
    smoke.add_argument('--out', type=Path, required=True)
    smoke.add_argument('--scope', choices=['standalone', 'framework_adapter'], default='standalone')
    smoke.add_argument('--expected-framework-revision')
    args = parser.parse_args(argv)
    try:
        if args.command == 'snapshot':
            result = build_source_bundle(args.package_root, args.out, docs=args.doc, bundle_id=args.bundle_id,
                a4_source_version=args.source_version, source_revision=args.source_revision, include_legacy_prompt=args.legacy_prompt)
        elif args.command == 'verify':
            result = verify_source_bundle(args.bundle, args.receipt)
            if args.extract and result.status == 'verified':
                extract_source_bundle(args.bundle, args.receipt, args.extract)
        else:
            result = run_offline_smoke(args.bundle_root, args.out, scope=args.scope,
                                       expected_framework_revision=args.expected_framework_revision)
        print(result.model_dump_json(indent=2))
        return 0 if getattr(result, 'status', 'verified') in ('verified', 'passed') else 2
    except (InputError, OSError, ValueError):
        print('a4_source_delivery_failed; existing outputs preserved; no install or model requests')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

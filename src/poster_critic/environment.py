"""A4 environment evidence with real local probes and no credential inspection."""

from __future__ import annotations

import argparse
from importlib import metadata, resources, util
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import tempfile
from typing import Annotated, Literal
import unicodedata

from pydantic import Field, StringConstraints, field_validator, model_validator

from .contracts import Contract, Text
from .errors import InputError
from .identity import fingerprint

Digest = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]


class EnvironmentDependencyV1(Contract):
    name: Text
    version: Text | None
    present: bool
    requirement_role: Literal['core', 'live', 'framework']

    @model_validator(mode='after')
    def consistent_presence(self):
        if self.present != (self.version is not None):
            raise ValueError('dependency presence requires a known installed version')
        return self


class CapabilityCheckV1(Contract):
    check_id: Text
    status: Literal['passed', 'failed', 'blocked', 'not_assessed']
    reason_code: Text


class EnvironmentSnapshotV1(Contract):
    schema_version: Literal[1] = 1
    kind: Literal['a4_environment'] = 'a4_environment'
    scope: Literal['standalone', 'framework_adapter']
    expected_framework_revision: Text | None
    observed_framework_revision: Text | None
    python_implementation: Text
    python_version: Text
    os: Text
    architecture: Text
    sqlite_version: Text
    unicode_data_version: Text
    dependencies: tuple[EnvironmentDependencyV1, ...]
    import_origin: Literal['source_bundle', 'installed_wheel', 'workspace', 'unknown']
    capabilities: tuple[CapabilityCheckV1, ...]
    environment_sha256: Digest

    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int:
            raise ValueError('environment version must be an integer')
        return value

    @model_validator(mode='after')
    def stable_seal(self):
        if self.scope == 'standalone' and (self.expected_framework_revision is not None or self.observed_framework_revision is not None):
            raise ValueError('standalone does not claim framework revision verification')
        names = [d.name for d in self.dependencies]
        checks = [c.check_id for c in self.capabilities]
        if names != sorted(set(names)) or checks != sorted(set(checks)):
            raise ValueError('environment dependencies/checks must be unique and sorted')
        if fingerprint(self.model_dump(mode='json', exclude={'environment_sha256'})) != self.environment_sha256:
            raise ValueError('environment seal differs')
        return self


def _dependency(name, role):
    try:
        version = metadata.version(name)
    except metadata.PackageNotFoundError:
        version = None
    return EnvironmentDependencyV1(name=name, version=version, present=version is not None, requirement_role=role)


def _origin(bundle_root):
    here = Path(__file__).resolve().parent
    if bundle_root is not None:
        supplied = Path(bundle_root).expanduser().absolute()
        if here == (supplied / 'src/poster_critic').resolve() and (supplied / 'manifest.json').is_file():
            return 'source_bundle'
        return 'unknown'
    if 'site-packages' in here.parts or 'dist-packages' in here.parts:
        return 'installed_wheel'
    if here.parent.name == 'src' and (here.parent.parent / '.git').exists():
        return 'workspace'
    return 'unknown'


def _resource_check():
    required = ('prompts/critic_v2.txt', 'prompts/critic_v3.txt', 'prompts/critic_v4.txt',
                'annotation.js', 'resources/models.example.json', 'resources/vision_ocr.swift')
    try:
        package = resources.files('poster_critic')
        for name in required:
            if not package.joinpath(name).read_bytes().strip():
                return CapabilityCheckV1(check_id='a4_resources', status='failed', reason_code='resource_empty')
        # Parse the example with the existing strict typed allowlist, without keys.
        from .providers.chat import ModelCatalog
        ModelCatalog.model_validate_json(package.joinpath('resources/models.example.json').read_bytes())
    except (OSError, ValueError, TypeError, ModuleNotFoundError):
        return CapabilityCheckV1(check_id='a4_resources', status='failed', reason_code='resource_invalid_or_missing')
    return CapabilityCheckV1(check_id='a4_resources', status='passed', reason_code='resources_read_and_template_validated')


def _filesystem_checks():
    result = []
    with tempfile.TemporaryDirectory(prefix='a4-environment-') as temp:
        root = Path(temp)
        try:
            if os.name != 'posix':
                raise ModuleNotFoundError
            import fcntl
            first = (root / 'lock').open('a+b')
            second = (root / 'lock').open('a+b')
            try:
                fcntl.flock(first, fcntl.LOCK_EX | fcntl.LOCK_NB)
                blocked = False
                try:
                    fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    blocked = True
                if not blocked:
                    raise ValueError('exclusive lock not enforced')
                fcntl.flock(first, fcntl.LOCK_UN)
                fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(second, fcntl.LOCK_UN)
            finally:
                first.close(); second.close()
            result.append(CapabilityCheckV1(check_id='posix_lock', status='passed', reason_code='local_exclusive_lock_probed'))
        except ModuleNotFoundError:
            result.append(CapabilityCheckV1(check_id='posix_lock', status='blocked', reason_code='posix_lock_unavailable'))
        except (OSError, ValueError):
            result.append(CapabilityCheckV1(check_id='posix_lock', status='failed', reason_code='local_lock_probe_failed'))
        try:
            source, destination = root / 'source', root / 'atomic'
            source.write_bytes(b'a4-local-probe')
            os.link(source, destination)
            if source.stat().st_ino != destination.stat().st_ino:
                raise OSError
            try:
                os.link(source, destination)
            except FileExistsError:
                pass
            else:
                raise OSError
            result.append(CapabilityCheckV1(check_id='hardlink_atomic_create', status='passed', reason_code='local_create_if_absent_probed'))
        except (OSError, AttributeError):
            result.append(CapabilityCheckV1(check_id='hardlink_atomic_create', status='failed', reason_code='local_hardlink_probe_failed'))
        try:
            with sqlite3.connect(root / 'ledger.sqlite') as connection:
                connection.execute('CREATE TABLE probe (value INTEGER)')
                connection.execute('INSERT INTO probe VALUES (1)')
                connection.commit()
                if connection.execute('SELECT value FROM probe').fetchone() != (1,):
                    raise sqlite3.DatabaseError
            result.append(CapabilityCheckV1(check_id='sqlite_local_write', status='passed', reason_code='local_sqlite_transaction_probed'))
        except (sqlite3.Error, OSError):
            result.append(CapabilityCheckV1(check_id='sqlite_local_write', status='failed', reason_code='local_sqlite_probe_failed'))
    return result


def _framework_revision():
    """Read source HEAD only when actual framework files live in that repository."""
    try:
        spec = util.find_spec('poster_framework')
        if spec is None or spec.origin is None:
            return None, 'framework_not_available'
        source = Path(spec.origin).resolve().parent
        if source.name != 'poster_framework' or source.parent.name != 'src':
            return None, 'framework_source_revision_unproven'
        repo = source.parent.parent
        if not (repo / '.git').exists():
            return None, 'framework_source_revision_unproven'
        head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True,
                              text=True, timeout=5, check=True).stdout.strip()
        changed = subprocess.run(['git', '-C', str(repo), 'status', '--porcelain', '--', 'src/poster_framework'],
                                 capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        if changed:
            return head, 'framework_source_dirty'
        return head, 'framework_source_revision_verified'
    except (OSError, subprocess.SubprocessError, ValueError):
        return None, 'framework_source_revision_unproven'


def collect_environment(*, scope: str = 'standalone', expected_framework_revision=None,
                        require_live: bool = False, source_bundle_root=None) -> EnvironmentSnapshotV1:
    """Collect sanitized stable evidence. Filesystem probes use a fresh temp dir."""
    if scope not in ('standalone', 'framework_adapter') or type(require_live) is not bool:
        raise InputError('choose an explicit environment scope and boolean live dependency selection')
    if expected_framework_revision is not None and (type(expected_framework_revision) is not str
            or not expected_framework_revision.strip() or len(expected_framework_revision) > 128
            or any(c.isspace() for c in expected_framework_revision)
            or expected_framework_revision.startswith(('sk-', 'data:'))):
        raise InputError('framework revision must be a short non-secret source identifier')
    if scope == 'standalone' and expected_framework_revision is not None:
        raise InputError('standalone environment does not accept a framework revision')
    dependencies = [_dependency('Pillow', 'core'), _dependency('pydantic', 'core')]
    if require_live:
        dependencies.append(_dependency('httpx', 'live'))
    if scope == 'framework_adapter':
        dependencies.extend((_dependency('langgraph', 'framework'), _dependency('poster-framework', 'framework')))
    capabilities = []
    for dependency in dependencies:
        capabilities.append(CapabilityCheckV1(check_id='dependency_' + dependency.name,
            status='passed' if dependency.present else 'blocked',
            reason_code='installed_version_observed' if dependency.present else 'dependency_missing'))
    try:
        from PIL import Image
        image = Image.new('RGB', (2, 2), (1, 2, 3))
        if image.getpixel((1, 1)) != (1, 2, 3):
            raise ValueError
        capabilities.append(CapabilityCheckV1(check_id='pillow_function', status='passed', reason_code='pixel_creation_probed'))
    except (ImportError, ValueError, OSError):
        capabilities.append(CapabilityCheckV1(check_id='pillow_function', status='blocked', reason_code='pillow_unusable'))
    try:
        if CapabilityCheckV1(check_id='probe', status='passed', reason_code='probe').status != 'passed':
            raise ValueError
        capabilities.append(CapabilityCheckV1(check_id='pydantic_function', status='passed', reason_code='strict_contract_probed'))
    except ValueError:
        capabilities.append(CapabilityCheckV1(check_id='pydantic_function', status='failed', reason_code='pydantic_contract_failed'))
    capabilities.append(_resource_check())
    try:
        capabilities.extend(_filesystem_checks())
    except OSError:
        capabilities.append(CapabilityCheckV1(check_id='filesystem_probe', status='blocked', reason_code='temporary_directory_unavailable'))
    implementation, version, system, machine = platform.python_implementation(), platform.python_version(), platform.system(), platform.machine()
    supported = implementation == 'CPython' and platform.python_version_tuple()[:2] == ('3', '11') and system in ('Darwin', 'Linux')
    capabilities.append(CapabilityCheckV1(check_id='runtime_target', status='passed' if supported else 'blocked',
        reason_code='supported_posix_cpython311' if supported else 'runtime_outside_first_version_scope'))
    observed = None
    if scope == 'framework_adapter':
        if expected_framework_revision is None:
            reason = 'explicit_framework_revision_required'
        else:
            observed, reason = _framework_revision()
        matched = expected_framework_revision is not None and observed == expected_framework_revision and reason == 'framework_source_revision_verified'
        if expected_framework_revision is not None and observed is not None and observed != expected_framework_revision:
            reason = 'framework_revision_mismatch'
        capabilities.append(CapabilityCheckV1(check_id='framework_revision', status='passed' if matched else 'blocked',
            reason_code=reason if matched or expected_framework_revision is not None else 'explicit_framework_revision_required'))
    else:
        capabilities.append(CapabilityCheckV1(check_id='framework_revision', status='not_assessed', reason_code='standalone_scope'))
    body = dict(schema_version=1, kind='a4_environment', scope=scope,
        expected_framework_revision=expected_framework_revision, observed_framework_revision=observed,
        python_implementation=implementation, python_version=version, os=system, architecture=machine,
        sqlite_version=sqlite3.sqlite_version, unicode_data_version=unicodedata.unidata_version,
        dependencies=[d.model_dump(mode='json') for d in sorted(dependencies, key=lambda d: d.name)],
        import_origin=_origin(source_bundle_root),
        capabilities=[c.model_dump(mode='json') for c in sorted(capabilities, key=lambda c: c.check_id)])
    return EnvironmentSnapshotV1.model_validate_json(json.dumps(dict(body, environment_sha256=fingerprint(body))))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', choices=['standalone', 'framework_adapter'], default='standalone')
    parser.add_argument('--expected-framework-revision')
    parser.add_argument('--require-live', action='store_true')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    try:
        value = collect_environment(scope=args.scope, expected_framework_revision=args.expected_framework_revision,
                                    require_live=args.require_live)
        if args.out:
            from .reporting import write_json
            write_json(args.out, value.model_dump(mode='json'))
        print(value.model_dump_json(indent=2))
        return 0 if all(c.status in ('passed', 'not_assessed') for c in value.capabilities) else 2
    except (InputError, OSError, ValueError):
        print('a4_environment_failed; no credentials or user paths collected')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

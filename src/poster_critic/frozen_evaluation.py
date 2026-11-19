"""Contained evaluation bundles and offline replay. Never load keys or call a network."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
from importlib.resources import files
import json
from pathlib import Path, PurePosixPath
import tempfile
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, ValidationError, field_validator, model_validator

from .adapters.local_input import MAX_IMAGE_BYTES, load_input
from .contracts import Contract, LocalManifest, Text
from .data import LabelSheet, label_value, validate_labels
from .engine import PROMPT_VERSION
from .errors import InputError
from .evaluate import Dataset, run_dataset, summarize_run
from .evaluation_io import read_results, read_run
from .identity import fingerprint, input_fingerprint
from .providers.chat import ModelCatalog, ProviderConfig
from .reporting import write_bytes, write_json

SHA256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

# Deliberate evaluation scope, not a recursive checkout or cache/environment snapshot.
# A change to this list changes the bundle verifier itself and therefore also its hash.
SOFTWARE_FILES = (
    '__init__.py', 'contracts.py', 'checks.py', 'policy.py', 'diagnostics.py',
    'engine.py', 'runtime.py', 'ports.py', 'errors.py', 'dispatch.py', 'cli.py',
    'delivery.py', 'structured_diagnostics.py', 'evidence.py',
    'providers/__init__.py', 'providers/chat.py', 'providers/fake.py',
    'evaluate.py', 'evaluation_io.py', 'frozen_evaluation.py', 'identity.py',
    'reporting.py', 'data.py', 'adapters/__init__.py', 'adapters/local_input.py',
    'adapters/framework_input.py', 'adapters/framework_executor.py',
    'adapters/framework_agent.py', 'prompts/critic_v2.txt',
    'prompts/critic_v3.txt', 'prompts/critic_v4.txt',
)


def _relative(value: str) -> str:
    """Use one portable spelling and reject traversal even when it resolves inside."""
    path = PurePosixPath(value)
    if (not value or '\\' in value or ':' in value or '\x00' in value
            or path.is_absolute() or any(part in ('', '.', '..') for part in value.split('/'))
            or path.as_posix() != value):
        raise InputError('references must be contained portable relative paths without traversal')
    return value


def _safe_path(path: Path, *, file: bool = True) -> Path:
    """Reject symlinks in the supplied path and all its ancestors before resolving."""
    absolute = path.expanduser().absolute()
    if any(part.is_symlink() for part in (absolute, *absolute.parents)):
        raise InputError('symlinks are not allowed in frozen evaluation paths')
    if (file and not absolute.is_file()) or (not file and not absolute.is_dir()):
        raise InputError('frozen evaluation path does not exist or has the wrong kind')
    return absolute.resolve()


def _reference(root: Path, value: str) -> Path:
    path = _safe_path(root / _relative(value))
    if not path.is_relative_to(root):
        raise InputError('reference leaves the evaluation root')
    return path


def _read(path: Path, *, limit: int = 10 * 1024 * 1024) -> bytes:
    try:
        with path.open('rb') as source:
            value = source.read(limit + 1)
    except OSError:
        raise InputError('cannot read frozen evaluation source') from None
    if len(value) > limit:
        raise InputError('frozen evaluation source exceeds its size limit')
    return value


def _decode_json(raw: bytes):
    def object_pairs(pairs):
        value = {}
        for name, item in pairs:
            if name in value:
                raise ValueError('duplicate JSON key')
            value[name] = item
        return value
    def nonfinite(_):
        raise ValueError('nonfinite JSON value')
    return json.loads(raw, object_pairs_hook=object_pairs, parse_constant=nonfinite)


def _parse(raw: bytes, model):
    try:
        value = _decode_json(raw)
        if (not isinstance(value, dict) or ('schema_version' in value
                and type(value['schema_version']) is not int)):
            raise ValueError('expected object with integer version')
        return model.model_validate_json(raw)
    except (ValueError, UnicodeError, ValidationError):
        # Never include source JSON or validation values; configs may contain a mistaken key.
        raise InputError(f'invalid {model.__name__} evaluation source') from None


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + '\n').encode('utf-8')


def _config_bytes(path: Path) -> bytes:
    raw = _read(_safe_path(path))
    try:
        value = _decode_json(raw)
    except (ValueError, UnicodeError):
        raise InputError('invalid safe provider configuration') from None
    model = ModelCatalog if isinstance(value, dict) and value.get('schema_version') == 2 else ProviderConfig
    config = _parse(raw, model)
    # Typed allow-list only. There is no os.environ access or ChatProvider construction.
    return _json_bytes(config.model_dump(mode='json'))


def _current_software() -> dict[str, bytes]:
    package = files('poster_critic')
    try:
        return {name: package.joinpath(name).read_bytes() for name in SOFTWARE_FILES}
    except OSError:
        raise InputError('current A4 evaluation software snapshot is unavailable') from None


class FrozenArtifactV1(Contract):
    path: Text
    sha256: SHA256
    size_bytes: int = Field(ge=0, le=MAX_IMAGE_BYTES)
    kind: Literal['dataset', 'input', 'image', 'observation', 'labels', 'config', 'software']

    @field_validator('path')
    @classmethod
    def portable_path(cls, value: str) -> str:
        return _relative(value)


class FrozenSampleV1(Contract):
    sample_id: Text
    input_sha256: SHA256
    image_sha256: SHA256


class FrozenEvaluationManifestV1(Contract):
    schema_version: Literal[1] = 1
    kind: Literal['a4_frozen_evaluation'] = 'a4_frozen_evaluation'
    purpose: Literal['software_demo'] = 'software_demo'
    source_purpose: Literal['software_demo', 'human_labeled']
    prompt_version: Text
    dataset_path: Literal['dataset.json'] = 'dataset.json'
    config_path: Literal['config.json'] | None = None
    label_source_path: Literal['labels.json'] | None = None
    software_files: tuple[Text, ...]
    artifacts: tuple[FrozenArtifactV1, ...] = Field(min_length=1)
    samples: tuple[FrozenSampleV1, ...] = Field(min_length=1)
    observations_complete: bool
    quality_thresholds: Literal['not_agreed'] = 'not_agreed'
    formal_acceptance: Literal['not_assessed'] = 'not_assessed'
    seal_sha256: SHA256

    @field_validator('schema_version', mode='before')
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError('schema version must be an integer')
        return value

    @model_validator(mode='after')
    def seal(self):
        paths = [item.path for item in self.artifacts]
        if len(paths) != len(set(paths)) or 'manifest.json' in paths or '.incomplete.json' in paths:
            raise ValueError('invalid artifact inventory')
        if len({sample.sample_id for sample in self.samples}) != len(self.samples):
            raise ValueError('duplicate frozen sample IDs')
        if fingerprint(self.model_dump(mode='json', exclude={'seal_sha256'})) != self.seal_sha256:
            raise ValueError('frozen manifest seal differs')
        return self


def _prepare_dataset(dataset_path: Path, *, label_source: Path | None = None):
    """Capture bytes once, then validate the exact bytes that will enter the bundle."""
    path = _safe_path(dataset_path)
    dataset = _parse(_read(path), Dataset)
    root = path.parent
    contents: dict[str, tuple[bytes, str]] = {}
    rows, audits, hashes = [], {}, {}
    for number, sample in enumerate(dataset.samples):
        if any(not value.strip() or value.strip().upper().startswith('TODO')
               for value in (sample.source_group, sample.source)):
            raise InputError('dataset requires explicit source and source_group')
        if sample.expected_decision == 'fail' and not sample.expected_issue_codes:
            raise InputError('fail labels need explicit expected_issue_codes')
        input_path = _reference(root, sample.input_path)
        local = _parse(_read(input_path), LocalManifest)
        image_path = _reference(input_path.parent, local.image.path)
        if not image_path.is_relative_to(root):
            raise InputError('image leaves dataset root')
        image = _read(image_path, limit=MAX_IMAGE_BYTES)
        suffix = '.png' if image.startswith(b'\x89PNG\r\n\x1a\n') else '.jpg'
        directory = f'samples/{number:04d}'
        image_name = 'image' + suffix
        manifest_value = local.model_dump(mode='json')
        manifest_value['image'] = {'path': image_name}
        manifest_raw = _json_bytes(manifest_value)
        # load_input validates image bytes, 720x1280, full manifest, and required_text normalization.
        with tempfile.TemporaryDirectory(prefix='a4-freeze-input-') as temporary:
            temporary_path = Path(temporary)
            (temporary_path / 'input.json').write_bytes(manifest_raw)
            (temporary_path / image_name).write_bytes(image)
            audit = load_input(temporary_path / 'input.json')
        if audit.sample_id != sample.sample_id:
            raise InputError('dataset sample ID differs from LocalManifest')
        if hashes.setdefault(audit.image.sha256, sample.split) != sample.split:
            raise InputError('identical image crosses dev/validation splits')
        audits[sample.sample_id] = audit
        contents[f'{directory}/input.json'] = (manifest_raw, 'input')
        contents[f'{directory}/{image_name}'] = (image, 'image')
        row = sample.model_dump(mode='json')
        row['input_path'] = f'{directory}/input.json'
        if sample.response_path is not None:
            response = _read(_reference(root, sample.response_path))
            try:
                response.decode('utf-8')
            except UnicodeError:
                raise InputError('fixed observations must be UTF-8 text') from None
            # Invalid JSON/observations remain fixed negative protocol examples for replay.
            row['response_path'] = f'{directory}/observation.json'
            contents[row['response_path']] = (response, 'observation')
        rows.append(row)
    label_path = None
    if label_source is not None:
        sheet = _parse(_read(_safe_path(label_source)), LabelSheet)
        validate_labels(sheet, audits)
        expected = {sample.sample_id: sample for sample in dataset.samples}
        for item in sheet.items:
            sample = expected[item.sample_id]
            target = (sample.expected_decision, tuple(sorted(set(sample.expected_issue_codes))),
                      tuple(sorted(set(sample.allowed_repair_stages)))
                      if sample.allowed_repair_stages is not None else None)
            if label_value(item) != target:
                raise InputError('dataset labels differ from the supplied label source')
        label_path = 'labels.json'
        contents[label_path] = (_json_bytes(sheet.model_dump(mode='json')), 'labels')
    canonical = {'schema_version': 1, 'purpose': 'software_demo', 'samples': rows}
    _parse(_json_bytes(canonical), Dataset)
    contents['dataset.json'] = (_json_bytes(canonical), 'dataset')
    bindings = tuple(FrozenSampleV1(sample_id=sample.sample_id,
                    input_sha256=input_fingerprint(audits[sample.sample_id]),
                    image_sha256=audits[sample.sample_id].image.sha256) for sample in dataset.samples)
    return dataset, contents, bindings, label_path


def freeze_evaluation(dataset_path: str | Path, output_dir: str | Path, *,
                      config_path: str | Path | None = None,
                      label_source: str | Path | None = None) -> FrozenEvaluationManifestV1:
    """Freeze safe sources to a new directory; a failed publication stays visibly incomplete."""
    dataset, contents, bindings, label_path = _prepare_dataset(Path(dataset_path),
        label_source=Path(label_source) if label_source is not None else None)
    if config_path is not None:
        contents['config.json'] = (_config_bytes(Path(config_path)), 'config')
    for name, raw in _current_software().items():
        contents['software/' + name] = (raw, 'software')
    value = dict(schema_version=1, kind='a4_frozen_evaluation', purpose='software_demo',
        source_purpose=dataset.purpose, prompt_version=PROMPT_VERSION,
        dataset_path='dataset.json', config_path='config.json' if config_path is not None else None,
        label_source_path=label_path, software_files=list(SOFTWARE_FILES),
        artifacts=[{'path': name, 'sha256': hashlib.sha256(raw).hexdigest(),
                    'size_bytes': len(raw), 'kind': kind} for name, (raw, kind) in sorted(contents.items())],
        samples=[sample.model_dump(mode='json') for sample in bindings],
        observations_complete=all(sample.response_path is not None for sample in dataset.samples),
        quality_thresholds='not_agreed', formal_acceptance='not_assessed')
    manifest = _parse(_json_bytes({**value, 'seal_sha256': fingerprint(value)}), FrozenEvaluationManifestV1)
    out = Path(output_dir).expanduser().absolute()
    # No destination replacement. Parent symlinks, including dangling ones, are rejected.
    if any(part.is_symlink() for part in (out, *out.parents)):
        raise InputError('symlinks are not allowed in frozen evaluation destinations')
    if out.exists():
        raise InputError('frozen evaluation destination already exists')
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / '.incomplete.json', {'kind': 'a4_incomplete_freeze', 'network_requests': 0})
    for name, (raw, _) in sorted(contents.items()):
        write_bytes(out / name, raw)
    write_json(out / 'manifest.json', manifest.model_dump(mode='json'))
    (out / '.incomplete.json').unlink()
    return manifest


def verify_frozen_evaluation(bundle_dir: str | Path, *,
                             config_path: str | Path | None = None) -> FrozenEvaluationManifestV1:
    """Reject incomplete/tampered bundles and current selected-code/configuration drift."""
    root = _safe_path(Path(bundle_dir), file=False)
    if (root / '.incomplete.json').exists() or not (root / 'manifest.json').is_file():
        raise InputError('incomplete frozen evaluation bundle; choose a new freeze destination')
    manifest = _parse(_read(_reference(root, 'manifest.json')), FrozenEvaluationManifestV1)
    if manifest.software_files != SOFTWARE_FILES or manifest.prompt_version != PROMPT_VERSION:
        raise InputError('current A4 software selection or prompt version differs from frozen bundle')
    inventory = {item.path: item for item in manifest.artifacts}
    actual = set()
    for path in root.rglob('*'):
        if path.is_symlink():
            raise InputError('symlink found in frozen evaluation bundle')
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    if actual != set(inventory) | {'manifest.json'}:
        raise InputError('frozen evaluation file inventory differs')
    for name, item in inventory.items():
        raw = _read(_reference(root, name), limit=max(MAX_IMAGE_BYTES, item.size_bytes))
        if len(raw) != item.size_bytes or hashlib.sha256(raw).hexdigest() != item.sha256:
            raise InputError('frozen evaluation artifact hash differs')
    for name, raw in _current_software().items():
        item = inventory.get('software/' + name)
        if item is None or item.kind != 'software' or item.sha256 != hashlib.sha256(raw).hexdigest():
            raise InputError('current A4 software differs from frozen bundle; freeze a new version')
    if (manifest.config_path is None) != ('config.json' not in inventory):
        raise InputError('frozen config inventory differs')
    if manifest.config_path is not None:
        # Revalidation ensures a sealed config cannot smuggle undeclared secret fields.
        captured = _config_bytes(_reference(root, manifest.config_path))
        if hashlib.sha256(captured).hexdigest() != inventory['config.json'].sha256:
            raise InputError('frozen configuration is not canonical')
        if config_path is None:
            raise InputError('provide explicit current config_path to check frozen provider configuration')
        if captured != _config_bytes(Path(config_path)):
            raise InputError('current provider configuration differs from frozen bundle')
    elif config_path is not None:
        raise InputError('bundle did not freeze provider configuration')
    dataset, prepared, bindings, label_path = _prepare_dataset(root / manifest.dataset_path,
        label_source=_reference(root, manifest.label_source_path) if manifest.label_source_path is not None else None)
    if (dataset.purpose != 'software_demo' or bindings != manifest.samples
            or label_path != manifest.label_source_path
            or manifest.observations_complete != all(sample.response_path is not None for sample in dataset.samples)):
        raise InputError('frozen dataset bindings or readiness differs')
    expected_kinds = {name: kind for name, (_, kind) in prepared.items()}
    expected_kinds.update({'software/' + name: 'software' for name in SOFTWARE_FILES})
    if manifest.config_path is not None:
        expected_kinds['config.json'] = 'config'
    if (set(expected_kinds) != set(inventory)
            or any(inventory[name].kind != kind for name, kind in expected_kinds.items())
            or any(hashlib.sha256(raw).hexdigest() != inventory[name].sha256
                   for name, (raw, _) in prepared.items())):
        raise InputError('frozen dataset canonical content or artifact kinds differ')
    return manifest


def _ratio(numerator: int, denominator: int) -> dict:
    return {'numerator': numerator, 'denominator': denominator,
            'rate': numerator / denominator if denominator else None}


async def replay_frozen_evaluation(bundle_dir: str | Path, output_dir: str | Path, *,
                                   split: str = 'dev', repeats: int = 1,
                                   max_attempts: int = 2, max_total_attempts: int = 24,
                                   config_path: str | Path | None = None) -> dict:
    """Replay stored observations only. Historical quality gates can never qualify this run."""
    manifest = verify_frozen_evaluation(bundle_dir, config_path=config_path)
    root = _safe_path(Path(bundle_dir), file=False)
    out = Path(output_dir).expanduser().absolute()
    if any(part.is_symlink() for part in (out, *out.parents)):
        raise InputError('symlinks are not allowed in replay destinations')
    if out.is_relative_to(root) or root.is_relative_to(out):
        raise InputError('replay output must be outside and separate from its frozen bundle')
    dataset = _parse(_read(root / manifest.dataset_path), Dataset)
    selected = [sample for sample in dataset.samples if sample.split == split]
    if any(sample.response_path is None for sample in selected):
        raise InputError('offline replay requires stored fixed observations for the selected split')
    # provider=None is mandatory: run_dataset builds only FakeProvider from stored observations.
    await run_dataset(root / manifest.dataset_path, out, split=split, repeats=repeats,
        max_attempts=max_attempts, max_total_attempts=max_total_attempts, provider=None)
    # Readers preload data, but an external writer may still alter the bundle/software
    # while an audit awaits. Keep the saved run, refuse a trusted completion receipt.
    if verify_frozen_evaluation(root, config_path=config_path) != manifest:
        raise InputError('frozen bundle identity changed during replay')
    summary = summarize_run(out)
    run = read_run(out)
    _, calls = read_results(out, run)
    if run['spec']['live'] or summary['usage']['live_attempts']:
        raise InputError('offline replay unexpectedly contains live calls')
    summary.update(frozen_bundle_seal_sha256=manifest.seal_sha256,
        purpose='software_demo', evidence_kind='software_demo_only',
        network_requests=0, quality_thresholds='not_agreed', formal_acceptance='not_assessed',
        acceptance={'status': 'not_assessed', 'reason': 'fixed observations test software, not model quality; thresholds not agreed'},
        observation_attempts={
            'valid': _ratio(sum(call.validation_status == 'valid' for call in calls), len(calls)),
            'invalid': _ratio(sum(call.validation_status == 'invalid' for call in calls), len(calls)),
            'not_validated': _ratio(sum(call.validation_status == 'not_run' for call in calls), len(calls)),
        },
        effective_observation_audits=_ratio(sum(call.validation_status == 'valid' for call in calls),
                                           summary['planned_audits']),
        token_accounting={'kind': 'not_assessed_for_fixed_observations', 'live_attempts': 0,
                          'note': 'Fake replay has no measured model tokens or service cost.'})
    write_json(out / 'summary.json', summary, replace=True)
    write_json(out / 'frozen_replay.json', {
        'schema_version': 1, 'bundle_seal_sha256': manifest.seal_sha256,
        'evidence_kind': 'software_demo_only', 'network_requests': 0,
        'quality_thresholds': 'not_agreed', 'formal_acceptance': 'not_assessed',
    })
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    freeze = commands.add_parser('freeze')
    freeze.add_argument('--dataset', type=Path, required=True)
    freeze.add_argument('--out', type=Path, required=True)
    freeze.add_argument('--config', type=Path)
    freeze.add_argument('--label-source', type=Path)
    verify = commands.add_parser('verify')
    verify.add_argument('--bundle', type=Path, required=True)
    verify.add_argument('--config', type=Path)
    replay = commands.add_parser('replay')
    replay.add_argument('--bundle', type=Path, required=True)
    replay.add_argument('--out', type=Path, required=True)
    replay.add_argument('--config', type=Path)
    replay.add_argument('--split', choices=['dev', 'validation'], default='dev')
    replay.add_argument('--repeats', type=int, choices=[1, 2], default=1)
    replay.add_argument('--attempts', type=int, choices=[1, 2], default=2)
    replay.add_argument('--max-total-attempts', type=int, default=24)
    args = parser.parse_args(argv)
    try:
        if args.command == 'freeze':
            result = freeze_evaluation(args.dataset, args.out, config_path=args.config, label_source=args.label_source)
            value = result.model_dump(mode='json')
        elif args.command == 'verify':
            result = verify_frozen_evaluation(args.bundle, config_path=args.config)
            value = {'status': 'verified', 'seal_sha256': result.seal_sha256,
                     'network_requests': 0, 'formal_acceptance': 'not_assessed'}
        else:
            value = asyncio.run(replay_frozen_evaluation(args.bundle, args.out, split=args.split,
                repeats=args.repeats, max_attempts=args.attempts,
                max_total_attempts=args.max_total_attempts, config_path=args.config))
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except InputError as exc:
        print(f'frozen evaluation error [InputError]: {exc}; no network calls')
        return 2
    except OSError:
        print('frozen evaluation error [OSError]: local read/publication failed; existing outputs are preserved; no network calls')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

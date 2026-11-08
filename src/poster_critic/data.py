"""Local dataset preparation and independent annotation. No model calls."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from typing import Literal

from PIL import Image
from pydantic import Field, ValidationError, model_validator

from .adapters.local_input import load_input
from .contracts import AuditInputV1, BBox, Contract, Status, Text
from .errors import InputError
from .evaluate import Dataset
from .identity import fingerprint, input_fingerprint
from .reporting import reserve_outputs, write_json


class DraftSample(Contract):
    sample_id: Text
    input_path: Text
    split: Literal['dev', 'validation', 'unassigned']
    source_group: str
    source: str
    expected_decision: Status | None = None
    expected_issue_codes: tuple[Text, ...] = ()
    allowed_repair_stages: tuple[Literal['brief', 'layout', 'asset', 'render'], ...] | None = None
    response_path: str | None = None


class DraftDataset(Contract):
    schema_version: Literal[1] = 1
    purpose: Literal['software_demo', 'human_labeled']
    samples: tuple[DraftSample, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def unique_ids(self):
        if len({s.sample_id for s in self.samples}) != len(self.samples):
            raise ValueError('duplicate sample IDs')
        return self


class AnnotationRegion(Contract):
    """Human evidence in the displayed 720x1280 image, not upstream layout proof."""

    bbox_xyxy: BBox
    issue_code: str = ''
    note: str = ''

    @model_validator(mode='after')
    def bounds(self):
        x1, y1, x2, y2 = self.bbox_xyxy
        if not (0 <= x1 < x2 <= 720 and 0 <= y1 < y2 <= 1280):
            raise ValueError('annotation region must have positive area within 720x1280')
        return self


class Label(Contract):
    sample_id: Text
    input_sha256: Text
    image_sha256: Text
    decision: Status | None = None
    issue_codes: tuple[Text, ...] = ()
    repair_stages: tuple[Literal['brief', 'layout', 'asset', 'render'], ...] | None = None
    evidence: str = ''
    regions: tuple[AnnotationRegion, ...] = Field(default=(), max_length=100)


class LabelSheet(Contract):
    schema_version: Literal[1] = 1
    annotator: Text
    items: tuple[Label, ...]

    @model_validator(mode='after')
    def unique_items(self):
        if len({i.sample_id for i in self.items}) != len(self.items):
            raise ValueError('duplicate label IDs')
        return self


def read_model(path: Path, model):
    try:
        return model.model_validate_json(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValidationError):
        raise InputError(f'invalid {model.__name__} file: {path}') from None


def inspect_dataset(path: Path) -> tuple[DraftDataset, dict[str, AuditInputV1], dict]:
    draft = read_model(path, DraftDataset)
    audits, errors, label_errors, pending, groups, images = {}, [], [], [], {}, {}
    for sample in draft.samples:
        prefix = sample.sample_id + ': '
        if sample.split == 'unassigned':
            errors.append(prefix + 'assign dev or validation split')
        for name in ('source_group', 'source'):
            value = getattr(sample, name)
            if not value.strip() or value.strip().upper().startswith('TODO'):
                errors.append(prefix + 'fill ' + name)
        if sample.source_group:
            if groups.setdefault(sample.source_group, sample.split) != sample.split:
                errors.append(prefix + 'source group crosses splits')
        try:
            audit = load_input(path.parent / sample.input_path)
            if audit.sample_id != sample.sample_id:
                raise InputError('manifest sample_id differs from dataset')
            if images.setdefault(audit.image.sha256, sample.split) != sample.split:
                raise InputError('identical image crosses splits')
            audits[sample.sample_id] = audit
        except InputError as exc:
            errors.append(prefix + str(exc))
        if sample.expected_decision is None:
            pending.append(sample.sample_id)
        elif sample.expected_decision == 'fail' and not sample.expected_issue_codes:
            label_errors.append(prefix + 'a fail label needs explicit expected_issue_codes')
    return draft, audits, {'network_requests': 0, 'samples': len(draft.samples),
                            'valid_inputs': len(audits), 'input_ready': not errors,
                            'labeled_ready': not errors and not pending and not label_errors,
                            'errors': errors, 'label_errors': label_errors, 'unlabeled_samples': pending}


def prepare(images: Path, out: Path) -> Path:
    candidates = sorted(p for p in images.rglob('*') if p.is_file() and p.suffix.lower() in ('.png', '.jpg', '.jpeg'))
    if not candidates:
        raise InputError('no PNG/JPEG images found')
    # Validate before creating output; preserve originals and never resize silently.
    for path in candidates:
        if path.stat().st_size > 20 * 1024 * 1024:
            raise InputError(f'image exceeds 20 MiB: {path}')
        with Image.open(path) as image:
            if image.size != (720, 1280) or image.format not in ('PNG', 'JPEG') or getattr(image, 'n_frames', 1) != 1:
                raise InputError(f'need a single 720x1280 PNG/JPEG: {path}')
            image.load()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'images').mkdir()
    samples = []
    for index, image in enumerate(candidates, 1):
        sample_id = f'sample-{index:04d}'
        destination = out / 'images' / (sample_id + image.suffix.lower())
        shutil.copyfile(image, destination)
        write_json(out / 'inputs' / f'{sample_id}.json', {
            'schema_version': 1, 'sample_id': sample_id,
            'image': {'path': '../images/' + destination.name},
            'keywords': [], 'user_constraints': [], 'approved_copy': None,
            'required_checks': ['keyword_alignment', 'user_constraints', 'text_readability', 'layout_quality'],
            'design_context': None, 'layout_context': None, 'asset_context': None, 'render_context': None,
        })
        samples.append({'sample_id': sample_id, 'input_path': f'inputs/{sample_id}.json',
                        'split': 'unassigned', 'source_group': '', 'source': '',
                        'expected_decision': None, 'expected_issue_codes': [], 'allowed_repair_stages': None})
    write_json(out / 'dataset.json', {'schema_version': 1, 'purpose': 'human_labeled', 'samples': samples})
    write_json(out / 'image_sources.json', [{'sample_id': s['sample_id'], 'original_path': str(p.resolve()),
                                            'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                                           for s, p in zip(samples, candidates)])
    return out / 'dataset.json'


def require_inputs(path: Path):
    draft, audits, state = inspect_dataset(path)
    if not state['input_ready']:
        raise InputError('dataset inputs are not ready; run data check and fix errors first')
    return draft, audits


def label_template(path: Path, annotator: str) -> LabelSheet:
    draft, audits = require_inputs(path)
    return LabelSheet(annotator=annotator, items=tuple(Label(
        sample_id=s.sample_id, input_sha256=input_fingerprint(audits[s.sample_id]),
        image_sha256=audits[s.sample_id].image.sha256) for s in draft.samples))


def validate_labels(sheet: LabelSheet, audits: dict, *, complete: bool = True, subset: bool = False) -> None:
    ids = {item.sample_id for item in sheet.items}
    if not ids.issubset(audits) or (not subset and ids != set(audits)):
        raise InputError('label IDs must match dataset inputs exactly')
    for item in sheet.items:
        audit = audits[item.sample_id]
        if item.input_sha256 != input_fingerprint(audit) or item.image_sha256 != audit.image.sha256:
            raise InputError('labels refer to different image or requirements; annotate again')
        if complete and (item.decision is None or not item.evidence.strip()
                         or (item.decision == 'fail' and not item.issue_codes)):
            raise InputError('complete every decision/evidence and label concrete issue codes for fail')


def label_value(item: Label) -> tuple:
    return item.decision, tuple(sorted(set(item.issue_codes))), (
        tuple(sorted(set(item.repair_stages))) if item.repair_stages is not None else None)


def compare_labels(dataset: Path, first: Path, second: Path) -> dict:
    _, audits = require_inputs(dataset)
    a, b = read_model(first, LabelSheet), read_model(second, LabelSheet)
    if a.annotator == b.annotator:
        raise InputError('two independently named annotators are required')
    for sheet in (a, b):
        validate_labels(sheet, audits)
    b_items = {item.sample_id: item for item in b.items}
    differences = [{'sample_id': item.sample_id, 'first': item.model_dump(mode='json'),
                    'second': b_items[item.sample_id].model_dump(mode='json')}
                   for item in a.items if label_value(item) != label_value(b_items[item.sample_id])]
    template = LabelSheet(annotator='adjudicator', items=tuple(Label(
        sample_id=row['sample_id'], input_sha256=input_fingerprint(audits[row['sample_id']]),
        image_sha256=audits[row['sample_id']].image.sha256) for row in differences))
    return {'network_requests': 0, 'compared': len(a.items), 'agreed': len(a.items) - len(differences),
            'differences': differences, 'resolution_template': template.model_dump(mode='json')}


def merge_labels(dataset: Path, first: Path, second: Path, out: Path, resolution: Path | None = None) -> None:
    comparison = compare_labels(dataset, first, second)
    draft, audits = require_inputs(dataset)
    a, b = read_model(first, LabelSheet), read_model(second, LabelSheet)
    chosen = {item.sample_id: item for item in a.items}
    conflicts = {row['sample_id'] for row in comparison['differences']}
    if conflicts:
        if resolution is None:
            raise InputError('annotations disagree; fill the comparison resolution_template and pass --resolution')
        decisions = read_model(resolution, LabelSheet)
        validate_labels(decisions, audits, subset=True)
        if {i.sample_id for i in decisions.items} != conflicts:
            raise InputError('resolution must cover exactly the conflicting samples')
        chosen.update({item.sample_id: item for item in decisions.items})
    rows = []
    for sample in draft.samples:
        item = chosen[sample.sample_id]
        row = sample.model_dump(mode='json')
        row.update(expected_decision=item.decision, expected_issue_codes=list(item.issue_codes),
                   allowed_repair_stages=list(item.repair_stages) if item.repair_stages is not None else None)
        # Moving the consensus file must not silently change relative input locations.
        row['input_path'] = str((dataset.parent / sample.input_path).resolve())
        if sample.response_path:
            row['response_path'] = str((dataset.parent / sample.response_path).resolve())
        rows.append(row)
    result = Dataset.model_validate_json(json.dumps({'schema_version': 1, 'purpose': 'human_labeled', 'samples': rows}))
    provenance = out.with_name(out.name + '.annotations.json')
    with reserve_outputs(out, provenance):
        write_json(provenance, {'first': a.model_dump(mode='json'), 'second': b.model_dump(mode='json'),
                               'resolution': read_model(resolution, LabelSheet).model_dump(mode='json') if conflicts else None,
                               'dataset_input_hashes': {key: input_fingerprint(value) for key, value in audits.items()}})
        write_json(out, result.model_dump(mode='json'))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init'); init.add_argument('--images', type=Path, required=True); init.add_argument('--out', type=Path, required=True)
    check = commands.add_parser('check'); check.add_argument('--dataset', type=Path, required=True)
    labels = commands.add_parser('labels'); labels.add_argument('--dataset', type=Path, required=True)
    labels.add_argument('--annotator', required=True); labels.add_argument('--out', type=Path, required=True)
    for name in ('compare', 'merge'):
        sub = commands.add_parser(name)
        for flag in ('dataset', 'first', 'second', 'out'):
            sub.add_argument('--' + flag, type=Path, required=True)
        if name == 'merge': sub.add_argument('--resolution', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'init': print(prepare(args.images, args.out))
        elif args.command == 'check':
            state = inspect_dataset(args.dataset)[2]; print(json.dumps(state, ensure_ascii=False, indent=2))
            return 0 if state['input_ready'] else 2
        elif args.command == 'labels': write_json(args.out, label_template(args.dataset, args.annotator).model_dump(mode='json'))
        elif args.command == 'compare': write_json(args.out, compare_labels(args.dataset, args.first, args.second))
        else: merge_labels(args.dataset, args.first, args.second, args.out, args.resolution)
        return 0
    except (InputError, OSError, ValueError) as exc:
        print(f'data error: {exc}'); return 2


if __name__ == '__main__':
    raise SystemExit(main())

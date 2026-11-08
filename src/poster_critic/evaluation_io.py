"""Atomic per-audit checkpoints; unfinished requests are never silently replayed."""

import json
import math
from pathlib import Path

from .contracts import CallRecordV2, parse_report
from .errors import InputError
from .identity import fingerprint
from .reporting import write_json, write_text


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict): raise ValueError('expected object')
        return value
    except (OSError, UnicodeError, ValueError):
        raise InputError(f'invalid evaluation state: {path.name}') from None


def read_run(out: Path) -> dict:
    run = read_json(out / 'run.json')
    if (run.get('schema_version') != 2 or not isinstance(run.get('spec'), dict)
            or fingerprint(run['spec']) != run.get('fingerprint')):
        raise InputError('not a resumable v2 evaluation run or manifest fingerprint mismatch')
    return run


def artifact_calls(value: dict, job: dict, spec: dict) -> tuple[CallRecordV2, ...]:
    try:
        if value.get('kind') == 'execution_error':
            if value['sample_id'] != job['sample_id'] or value['image_sha256'] != job['image_sha256']:
                raise ValueError('error belongs to another sample')
            calls = tuple(CallRecordV2.model_validate_json(json.dumps(c)) for c in value['calls'])
        else:
            report = parse_report(json.dumps(value))
            if report.schema_version not in (2, 3) or report.sample_id != job['sample_id'] or report.image_sha256 != job['image_sha256']:
                raise ValueError('report belongs to another input')
            calls = report.calls
        if (len(calls) > spec['max_attempts_per_audit']
                or any(c.attempt != i or c.model != spec['provider'] or c.live != spec['live'] for i, c in enumerate(calls, 1))):
            raise ValueError('inconsistent call records')
        return calls
    except (KeyError, ValueError, TypeError):
        raise InputError('inconsistent saved report/call records; refusing to continue') from None


def make_row(value: dict, calls: tuple, job: dict, index: int, elapsed_ms: float, *, recovered=False) -> dict:
    if (type(elapsed_ms) not in (int, float) or not math.isfinite(elapsed_ms)
            or elapsed_ms < 0 or type(recovered) is not bool):
        raise InputError('invalid saved latency metadata')
    error = value.get('kind') == 'execution_error'
    found = {issue['code'] for issue in value['issues']} if not error else set()
    hint = value.get('repair_hint')
    return {
        'sample_id': job['sample_id'], 'repeat': job['repeat'], 'image_sha256': job['image_sha256'],
        'expected_decision': job['expected_decision'], 'decision': None if error else value['decision'],
        'expected_issues_found': bool(job['expected_issue_codes']) and set(job['expected_issue_codes']).issubset(found),
        'observed_issue_codes': sorted(found), 'allowed_repair_stages': job['allowed_repair_stages'],
        'repair_stage': hint['stage'] if hint else None,
        'first_valid': bool(calls and calls[0].validation_status == 'valid'),
        'elapsed_ms': round(elapsed_ms, 3), 'latency_recovered': recovered,
        'error_type': value.get('error_type') if error else None,
        'report_path': f'{index:04d}.json', 'report_sha256': fingerprint(value), 'attempts': len(calls),
    }


def save_result(out: Path, index: int, value: dict, calls: tuple, job: dict, elapsed_ms: float, *, recovered=False) -> dict:
    artifact = out / f'{index:04d}.json'
    if not artifact.exists(): write_json(artifact, value)
    row = make_row(value, calls, job, index, elapsed_ms, recovered=recovered)
    write_json(out / f'{index:04d}.result.json', row)
    return row


def read_results(out: Path, run: dict, *, recover: bool = False) -> tuple[dict[int, dict], tuple]:
    rows, all_calls = {}, []
    spec = run['spec']
    for index, job in enumerate(spec['jobs']):
        artifact, result, pending = (out / f'{index:04d}{suffix}' for suffix in ('.json', '.result.json', '.pending.json'))
        if result.exists():
            value, row = read_json(artifact), read_json(result)
            calls = artifact_calls(value, job, spec)
            expected = make_row(value, calls, job, index, row.get('elapsed_ms', 0), recovered=row.get('latency_recovered', False))
            if row != expected: raise InputError('result row does not match its saved report and labels')
            rows[index] = row; all_calls.extend(calls)
        elif artifact.exists() or pending.exists():
            journal = read_json(pending) if pending.exists() else {'started_attempts': 0, 'calls': [], 'elapsed_ms': 0}
            if pending.exists() and (journal.get('run_fingerprint') != run['fingerprint'] or journal.get('index') != index):
                raise InputError('pending request belongs to a different run')
            started = journal.get('started_attempts')
            if (type(started) is not int or not 0 <= started <= spec['max_attempts_per_audit']
                    or not isinstance(journal.get('calls'), list) or len(journal['calls']) > started):
                raise InputError('invalid pending attempt count or records')
            if artifact.exists():
                value = read_json(artifact); calls = artifact_calls(value, job, spec)
            else:
                if started == 0:
                    continue  # Durable journal confirms no external request was started.
                value = {'kind': 'execution_error', 'error_type': 'InterruptedRun',
                         'sample_id': job['sample_id'], 'image_sha256': job['image_sha256'], 'calls': journal['calls']}
                existing = artifact_calls(value, job, spec)
                if len(existing) > started: raise InputError('pending records exceed started attempts')
                calls = list(existing)
                for number in range(len(calls) + 1, started + 1):
                    calls.append(CallRecordV2(attempt=number, model=spec['provider'], live=spec['live'],
                        status='error', elapsed_ms=0.0, error_type='InterruptedWithoutResponse',
                        transport_status='failed', validation_status='not_run',
                        billing_status='unknown' if spec['live'] else 'not_applicable'))
                value['calls'] = [c.model_dump(mode='json') for c in calls]
            if recover:
                rows[index] = save_result(out, index, value, calls, job, journal.get('elapsed_ms', 0), recovered=True)
            else:
                rows[index] = make_row(value, calls, job, index, journal.get('elapsed_ms', 0), recovered=True)
            all_calls.extend(calls)
    if len(all_calls) > spec['max_total_attempts']:
        raise InputError('saved calls exceed the cumulative request budget')
    return rows, tuple(all_calls)


def export_rows(out: Path, rows: dict[int, dict]) -> None:
    write_text(out / 'results.jsonl', ''.join(json.dumps(rows[i], ensure_ascii=False) + '\n' for i in sorted(rows)), replace=True)

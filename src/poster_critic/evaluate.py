"""Reproducible independent evaluation. Labels never enter the audit request."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from math import ceil
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from .adapters.local_input import load_input
from .contracts import Contract, Status, Text
from .engine import AuditEngine, PROMPT_VERSION
from .errors import BudgetExceededError, InputError, ProviderError
from .evaluation_io import (export_rows, read_results, read_run, save_result)
from .identity import fingerprint, input_fingerprint, software_fingerprint
from .providers.fake import FakeProvider
from .reporting import file_lock, usage_summary, write_json
from .runtime import StandaloneExecutor


class EvalSample(Contract):
    sample_id: Text
    input_path: Text
    split: Literal['dev', 'validation']
    source_group: Text
    source: Text
    expected_decision: Status
    expected_issue_codes: tuple[Text, ...] = ()
    allowed_repair_stages: tuple[Literal['brief', 'layout', 'asset', 'render'], ...] | None = None
    response_path: str | None = None


class Dataset(Contract):
    schema_version: Literal[1] = 1
    purpose: Literal['software_demo', 'human_labeled']
    samples: tuple[EvalSample, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def no_leakage(self) -> Dataset:
        ids, groups = set(), {}
        for sample in self.samples:
            if sample.sample_id in ids:
                raise ValueError('duplicate sample ID')
            ids.add(sample.sample_id)
            if sample.source_group in groups and groups[sample.source_group] != sample.split:
                raise ValueError('same source group cannot cross dev/validation')
            groups[sample.source_group] = sample.split
        return self


def _ratio(numerator: int, denominator: int) -> dict:
    return {'numerator': numerator, 'denominator': denominator,
            'rate': numerator / denominator if denominator else None}


def summarize(rows: list[dict]) -> dict:
    normal = [r for r in rows if r['expected_decision'] == 'pass']
    problems = [r for r in rows if r['expected_decision'] == 'fail']
    first_valid = sum(r['first_valid'] for r in rows)
    failed_or_unknown = sum(r['decision'] in (None, 'unknown') for r in rows)
    groups = {}
    for row in rows:
        groups.setdefault(row['sample_id'], []).append(row)
    paired = [g for g in groups.values() if len(g) == 2]
    times = sorted(r['elapsed_ms'] for r in rows)
    eligible = [r for r in rows if r['allowed_repair_stages'] is not None and r['allowed_repair_stages']]
    hint_correct = sum(r['decision'] == 'fail' and r['repair_stage'] in r['allowed_repair_stages'] for r in eligible)
    hinted = [r for r in rows if r['repair_stage'] is not None]
    return {
        'planned_audits': len(rows),
        'decision_agreement': _ratio(sum(r['decision'] == r['expected_decision'] for r in rows), len(rows)),
        'problem_detection': _ratio(sum(r['decision'] == 'fail' and r['expected_issues_found'] for r in problems), len(problems)),
        'normal_false_fail': _ratio(sum(r['decision'] == 'fail' for r in normal), len(normal)),
        'unknown_or_execution_failure': _ratio(failed_or_unknown, len(rows)),
        'first_valid_response': _ratio(first_valid, len(rows)),
        'repair_hint_correct_on_eligible': _ratio(hint_correct, len(eligible)),
        'repair_hint_coverage_on_eligible': _ratio(sum(r['repair_stage'] is not None for r in eligible), len(eligible)),
        'unexpected_repair_hints': sum(r['allowed_repair_stages'] == [] for r in hinted),
        'repeat_decision_consistency': _ratio(sum(g[0]['decision'] is not None and g[0]['decision'] == g[1]['decision'] for g in paired), len(paired)),
        'latency_ms': {'median': median(times) if times else None,
                       'p95_nearest_rank': times[ceil(.95 * len(times)) - 1] if times else None},
        'framework_target_legality': 'not_assessed; standalone audit only',
    }


def acceptance(rows: list[dict], *, evidence_kind: str, split: str) -> dict:
    """Project pilot thresholds; require the planned 24-image/two-repeat design."""
    groups = {number: [r for r in rows if r['repeat'] == number] for number in (1, 2)}
    valid_design = (len(rows) == 48 and all(
        len(group) == 24 and len({r['sample_id'] for r in group}) == 24
        and sum(r['expected_decision'] == 'pass' for r in group) == 8
        and sum(r['expected_decision'] == 'fail' for r in group) == 16
        for group in groups.values())
        and {r['sample_id'] for r in groups[1]} == {r['sample_id'] for r in groups[2]})
    if evidence_kind != 'live_model_evaluation' or split != 'validation' or not valid_design:
        return {'status': 'not_assessed', 'reason': 'requires live human-labeled validation: 24 images, 8 normal/16 problems, two repeats'}
    per_repeat = {str(n): summarize(group) for n, group in groups.items()}
    total = summarize(rows)
    gates = {
        'problem_detection_at_least_14_of_16_each_repeat': all(v['problem_detection']['numerator'] >= 14 for v in per_repeat.values()),
        'normal_false_fail_at_most_1_of_8_each_repeat': all(v['normal_false_fail']['numerator'] <= 1 for v in per_repeat.values()),
        'unknown_or_failure_at_most_2_of_24_each_repeat': all(v['unknown_or_execution_failure']['numerator'] <= 2 for v in per_repeat.values()),
        'first_valid_at_least_47_of_48': total['first_valid_response']['numerator'] >= 47,
        'consistent_decision_at_least_22_of_24': total['repeat_decision_consistency']['numerator'] >= 22,
    }
    hint_rate = total['repair_hint_correct_on_eligible']['rate']
    return {'status': 'pass' if all(gates.values()) else 'fail', 'quality_gates': gates,
            'repair_hint_gate': 'not_assessed' if hint_rate is None else 'pass' if hint_rate >= .9 else 'fail',
            'scope': 'Small project pilot only. Framework target legality and repair outcomes are not assessed.'}


def summarize_run(out: Path) -> dict:
    """Recompute saved metrics and known/unknown usage without any provider."""
    run = read_run(out)
    rows, calls = read_results(out, run)
    return _summary(run, rows, calls, complete=all((out / f'{i:04d}.result.json').exists()
                                                  for i in range(len(run['spec']['jobs']))))


def _summary(run: dict, rows: dict, calls: tuple, *, complete: bool) -> dict:
    spec = run['spec']
    result = summarize(list(rows.values()))
    result['planned_audits'] = len(spec['jobs'])
    result['recorded_audits'] = len(rows)
    result['complete'] = complete
    result['not_recorded_audits'] = len(spec['jobs']) - len(rows)
    result['note'] = ('Complete run.' if complete else
                      'Partial run: rates use recorded rows only, pending usage is included conservatively; no acceptance claim.')
    result['latency_recovered_count'] = sum(row.get('latency_recovered', False) for row in rows.values())
    result['usage'] = usage_summary(calls, input_price=spec['flat_price_per_million']['input'],
                                    output_price=spec['flat_price_per_million']['output'],
                                    currency=spec['flat_price_per_million']['currency'])
    result['evidence_kind'] = spec['evidence_kind']
    result['per_repeat'] = {str(n): summarize([r for r in rows.values() if r['repeat'] == n])
                            for n in range(1, spec['repeats'] + 1)}
    result['acceptance'] = acceptance(list(rows.values()),
        evidence_kind=spec['evidence_kind'] if complete else 'incomplete', split=spec['split'])
    return result


async def run_dataset(dataset_path: Path, out: Path, *, provider=None, split: str = 'dev',
                      repeats: int = 1, max_attempts: int = 2, max_total_attempts: int = 24,
                      request_timeout: float = 30.0, total_timeout: float = 90.0,
                      input_price: float | None = None, output_price: float | None = None,
                      currency: str | None = None, resume: bool = False) -> dict:
    if (type(repeats) is not int or repeats not in (1, 2)
            or type(max_attempts) is not int or max_attempts not in (1, 2)
            or type(max_total_attempts) is not int or max_total_attempts < 1):
        raise InputError('invalid evaluation repeat or attempt limits')
    if split not in ('dev', 'validation'):
        raise InputError('choose dev or validation split')
    try:
        raw = dataset_path.read_text(encoding='utf-8')
        dataset = Dataset.model_validate_json(raw)
    except (OSError, UnicodeError, ValidationError):
        raise InputError('invalid dataset; complete labels, unique IDs and nonoverlapping source groups are required') from None
    prepared, hashes, input_hashes = [], {}, {}
    for sample in dataset.samples:
        audit = load_input(dataset_path.parent / sample.input_path)
        if audit.sample_id != sample.sample_id:
            raise InputError('dataset sample ID does not match its local input')
        if hashes.setdefault(audit.image.sha256, sample.split) != sample.split:
            raise InputError('identical image appears in both dev and validation')
        input_hashes[sample.sample_id] = input_fingerprint(audit)
        if sample.split != split: continue
        replay, response_hash = None, None
        if provider is None:
            if not sample.response_path:
                raise InputError('fake evaluation requires explicit response_path for every selected sample')
            replay = FakeProvider.from_file(dataset_path.parent / sample.response_path)
            response_hash = hashlib.sha256((dataset_path.parent / sample.response_path).read_bytes()).hexdigest()
        prepared.append((sample, audit, replay, response_hash))
    if not prepared: raise InputError('selected split has no samples')
    StandaloneExecutor(max_attempts, request_timeout=request_timeout, total_timeout=total_timeout)
    usage_summary((), input_price=input_price, output_price=output_price, currency=currency)
    live = bool(provider and provider.live)
    evidence = 'live_model_evaluation' if live and dataset.purpose == 'human_labeled' else 'software_demo_only'
    jobs = [dict(sample.model_dump(mode='json'), repeat=repeat + 1, image_sha256=audit.image.sha256,
                 input_sha256=input_hashes[sample.sample_id], response_sha256=response_hash)
            for repeat in range(repeats) for sample, audit, _, response_hash in prepared]
    spec = {
        'purpose': dataset.purpose, 'live': live, 'evidence_kind': evidence,
        'dataset_sha256': hashlib.sha256(raw.encode()).hexdigest(), 'input_hashes': input_hashes,
        'split': split, 'repeats': repeats, 'jobs': jobs, 'software_sha256': software_fingerprint(),
        'prompt_version': PROMPT_VERSION, 'provider': provider.name if provider is not None else prepared[0][2].name,
        'max_total_attempts': max_total_attempts, 'max_attempts_per_audit': max_attempts,
        'request_timeout': request_timeout, 'total_timeout': total_timeout,
        'provider_config': provider.config.model_dump(mode='json') if hasattr(provider, 'config') else None,
        'flat_price_per_million': {'input': input_price, 'output': output_price, 'currency': currency},
    }
    if resume:
        if not out.is_dir(): raise InputError('resume requires an existing evaluation directory')
    else:
        out.mkdir(parents=True, exist_ok=False)
    with file_lock(out / '.run.lock'):
        if resume:
            run = read_run(out)
            if run['spec'] != spec:
                raise InputError('resume fingerprint differs: inputs, labels, replay, code, model or budgets changed; use a new run')
        else:
            run = {'schema_version': 2, 'fingerprint': fingerprint(spec), 'spec': spec,
                   'evidence_kind': evidence, 'planned_audits': len(jobs)}
            write_json(out / 'run.json', run)
        # Recover every saved slot and its accounting before issuing any new request.
        rows, old_calls = read_results(out, run, recover=True)
        all_calls = list(old_calls)
        for index, job in enumerate(jobs):
            if index in rows: continue
            sample, audit, replay, _ = prepared[index % len(prepared)]
            started = perf_counter()
            report, error, executor, interrupted = None, None, None, False
            remaining = max_total_attempts - len(all_calls)
            pending = out / f'{index:04d}.pending.json'
            def journal(started_attempts, calls):
                write_json(pending, {'run_fingerprint': run['fingerprint'], 'index': index,
                    'started_attempts': started_attempts, 'calls': [c.model_dump(mode='json') for c in calls],
                    'elapsed_ms': round((perf_counter() - started) * 1000, 3)}, replace=True)
            journal(0, ())
            try:
                if remaining <= 0: raise BudgetExceededError('evaluation request budget exhausted')
                limit = min(max_attempts, remaining)
                executor = StandaloneExecutor(limit, allow_live=live, request_timeout=request_timeout,
                                              total_timeout=total_timeout, on_update=journal)
                report = await AuditEngine(provider if provider is not None else replay, executor, max_retries=limit - 1).audit(audit)
            except (ProviderError, BudgetExceededError) as exc:
                error = type(exc).__name__
            except asyncio.CancelledError:
                error, interrupted = 'CancelledError', True
            calls = executor.records if executor else ()
            value = report.model_dump(mode='json') if report else {
                'kind': 'execution_error', 'error_type': error, 'sample_id': sample.sample_id,
                'image_sha256': audit.image.sha256, 'calls': [c.model_dump(mode='json') for c in calls],
            }
            rows[index] = save_result(out, index, value, calls, job, (perf_counter() - started) * 1000)
            all_calls.extend(calls)
            export_rows(out, rows)
            if interrupted: raise asyncio.CancelledError
        export_rows(out, rows)
        result = _summary(run, rows, tuple(all_calls), complete=True)
        write_json(out / 'summary.json', result, replace=resume)
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Independent evaluation; replay scores are not vision accuracy.')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--dataset', type=Path)
    source.add_argument('--summarize', type=Path, help='Read a saved v2 run without a provider')
    parser.add_argument('--out', type=Path, help='Run directory, or new JSON path with --summarize')
    parser.add_argument('--resume', action='store_true', help='Continue unchanged run; do not retry completed/error slots')
    parser.add_argument('--split', choices=['dev', 'validation'], default='dev')
    parser.add_argument('--repeats', type=int, choices=[1, 2], default=1)
    parser.add_argument('--provider', choices=['fake', 'chat'], default='fake')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--model', help='Named model catalog entry')
    parser.add_argument('--max-total-attempts', type=int, default=24)
    parser.add_argument('--attempts', type=int, choices=[1, 2], default=2)
    parser.add_argument('--timeout', type=float, default=30.0)
    parser.add_argument('--total-timeout', type=float, default=90.0)
    parser.add_argument('--input-price', type=float)
    parser.add_argument('--output-price', type=float)
    parser.add_argument('--currency')
    args = parser.parse_args(argv)
    try:
        if args.summarize:
            if args.resume or args.config or args.model or args.provider != 'fake':
                raise InputError('--summarize is read-only and does not accept provider/resume options')
            if not args.summarize.is_dir(): raise InputError('saved run directory not found')
            with file_lock(args.summarize / '.run.lock'):
                summary = summarize_run(args.summarize)
            if args.out: write_json(args.out, summary)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        if args.out is None: raise InputError('--dataset requires --out')
        provider = None
        if args.provider == 'chat':
            if not args.config:
                raise InputError('chat evaluation requires --config')
            from .providers.chat import ChatProvider, load_config
            provider = ChatProvider(load_config(args.config, args.model))
        elif args.config or args.model:
            raise InputError('fake evaluation does not use --config')
        summary = asyncio.run(run_dataset(args.dataset, args.out, provider=provider, split=args.split,
                                         repeats=args.repeats, max_total_attempts=args.max_total_attempts,
                                         max_attempts=args.attempts, request_timeout=args.timeout,
                                         total_timeout=args.total_timeout, input_price=args.input_price,
                                         output_price=args.output_price, currency=args.currency, resume=args.resume))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (InputError, ProviderError, OSError, ValueError) as exc:
        print(f'evaluation error: {exc}')
        return 2
    except KeyboardInterrupt:
        print('evaluation interrupted; completed rows and the interrupted attempt remain in the output directory')
        return 130


if __name__ == '__main__':
    raise SystemExit(main())

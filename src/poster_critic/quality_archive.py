"""Read-only V2 quality archive verification across different software versions.

Archived source is hashed, never imported or executed. Content and declared
protocol bindings are verified here. Hashes cannot authenticate annotators,
clock time, network transport, or invoices. Current runtime identity is not
substituted for the identity of a historical benchmark arm.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat

from .adapters.local_input import LocalManifest, load_input
from .contracts import AuditReportV3, CallRecordV2, parse_report
from .data import LabelSheet, label_value, validate_labels
from .dispatch import _bound_report
from .errors import InputError
from .evaluate import Dataset
from .evaluation_io import artifact_calls, read_results, read_run
from .experiment_identity import SourceFileV1
from .frozen_evaluation import _decode_json, _json_bytes, _parse, _reference, _safe_path
from .identity import fingerprint, input_fingerprint
from .language_profiles import LanguageProfileV1
from .provider_capabilities import ProviderCapabilityV1
from .providers.chat import ProviderConfig
from .quality_validation import (FrozenQualityManifestV2, QualityArtifactV1,
    QualityExecutionV2, QualityReceiptV2, QualitySampleBindingV1, QualityStartV2,
    ThresholdProfileV1, _artifacts_hash, _inventory_hash, _manifest_client_policy,
    _quality_policy_hash, _run_artifacts, _verify_v2_run_evidence,
    evaluate_quality_gates, frozen_secondary_policy, parse_frozen_quality_tool,
    planned_quality_metrics)
from .reporting import usage_summary

_LEGACY_FILES = ('contracts.py', 'checks.py', 'policy.py', 'diagnostics.py', 'engine.py',
    'runtime.py', 'ports.py', 'providers/chat.py', 'providers/fake.py', 'evaluate.py',
    'evaluation_io.py', 'identity.py', 'reporting.py', 'adapters/local_input.py',
    'prompts/critic_v2.txt', 'prompts/critic_v3.txt', 'prompts/critic_v4.txt')
_MAX_FILE = 20 * 1024 * 1024
_MAX_TOTAL = 256 * 1024 * 1024
_MAX_FILES = 5000


@dataclass(frozen=True)
class VerifiedQualityArchive:
    manifest: FrozenQualityManifestV2
    receipt: QualityReceiptV2
    dataset: Dataset
    labels: LabelSheet
    thresholds: ThresholdProfileV1
    jobs: tuple[dict, ...]
    rows: dict[int, dict]
    reports: dict[int, AuditReportV3 | None]
    calls: tuple[CallRecordV2, ...]
    case_calls: dict[int, tuple[CallRecordV2, ...]]
    artifacts: dict[str, str]
    manifest_file_sha256: str
    receipt_file_sha256: str
    execution_file_sha256: str
    agreement_file_sha256: str | None


def _read_regular(path: Path, *, validate_json: bool = True) -> bytes:
    path = _safe_path(path)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_FILE:
                raise InputError('quality archive requires bounded regular files')
            raw = handle.read(_MAX_FILE + 1)
            after = os.fstat(handle.fileno())
        if (len(raw) != before.st_size or len(raw) > _MAX_FILE
                or (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise InputError('quality archive file changed while being read')
        if validate_json and path.suffix == '.json':
            _decode_json(raw)
        elif validate_json and path.suffix == '.jsonl':
            for line in raw.splitlines():
                if line.strip():
                    _decode_json(line)
        return raw
    except (OSError, ValueError, UnicodeError):
        raise InputError('invalid bounded quality archive file') from None


def _snapshot(root: Path) -> dict[str, bytes]:
    values = {}
    size = 0
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise InputError('quality archive must not follow symlinks')
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        # Fixed negative observations may deliberately contain invalid JSON.
        # Their bytes/UTF-8 remain checked against the frozen inventory.
        raw = _read_regular(path, validate_json=not (
            relative.startswith('samples/') and relative.endswith('/observation.json')))
        size += len(raw)
        if len(values) >= _MAX_FILES or size > _MAX_TOTAL:
            raise InputError('quality archive exceeds its inventory limits')
        values[relative] = raw
    return values


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _bundle(root: Path, content: dict[str, bytes]):
    manifest = _parse(content.get('manifest.json', b''), FrozenQualityManifestV2)
    inventory = {a.path: a for a in manifest.artifacts}
    if set(content) != set(inventory) | {'manifest.json'}:
        raise InputError('archived quality bundle inventory differs')
    for name, item in inventory.items():
        if len(content[name]) != item.size_bytes or _digest(content[name]) != item.sha256:
            raise InputError('archived quality artifact hash differs')
    source = {name.removeprefix('software/'): raw for name, raw in content.items() if name.startswith('software/')}
    source_files = tuple(SourceFileV1(path='src/poster_critic/' + name, sha256=_digest(raw), size_bytes=len(raw))
                         for name, raw in sorted(source.items()))
    identity = manifest.experiment_identity
    if (source_files != identity.source_files or _inventory_hash(source) != manifest.source_inventory_sha256
            or fingerprint({name: _digest(source.get(name, b'')) for name in _LEGACY_FILES})
            != manifest.software_fingerprint_v1 or identity.prompt_version != manifest.prompt_version):
        raise InputError('archived source or prompt identity differs')
    thresholds = _parse(content.get('thresholds.json', b''), ThresholdProfileV1)
    if (fingerprint(thresholds.model_dump(mode='json')) != manifest.threshold_profile_sha256
            or thresholds.state != manifest.threshold_state or thresholds.repeats != manifest.plan.repeats):
        raise InputError('archived threshold binding differs')
    agreement = content.get('agreement.bin')
    if thresholds.state == 'agreed' and (agreement is None or not agreement.strip()
            or _digest(agreement) != thresholds.agreement_evidence_sha256):
        raise InputError('archived agreement evidence differs')
    dataset = _parse(content.get('dataset.json', b''), Dataset)
    labels = _parse(content.get('labels.json', b''), LabelSheet)
    config = _parse(content['config.json'], ProviderConfig) if 'config.json' in content else None
    language = _parse(content.get('language.json', b''), LanguageProfileV1)
    cap = _parse(content['capability.json'], ProviderCapabilityV1) if 'capability.json' in content else None
    if ((None if config is None else fingerprint(config.model_dump(mode='json'))) != manifest.provider_config_sha256
            or language.profile_sha256 != manifest.language_profile_sha256
            or (None if cap is None else cap.capability_sha256) != manifest.capability_sha256):
        raise InputError('archived selected policy hashes differ')
    if manifest.execution_mode == 'live' and (config is None or cap is None):
        raise InputError('archived live quality requires explicit provider and capability')
    if cap is not None and (config is None or config.profile != cap.provider_profile
            or config.model != cap.model_id or config.base_url not in cap.allowed_base_urls
            or manifest.endpoint_family != cap.endpoint_family or manifest.region != cap.region):
        raise InputError('archived provider capability combination differs')
    expected_kinds = {name: 'software' for name in inventory if name.startswith('software/')}
    expected_kinds.update({'dataset.json': 'dataset', 'labels.json': 'labels',
                           'thresholds.json': 'thresholds', 'language.json': 'language'})
    for name, kind in (('config.json','config'),('capability.json','capability'),('agreement.bin','agreement')):
        if name in content:
            expected_kinds[name] = kind
    audits, full_bindings = {}, []
    for number, sample in enumerate(dataset.samples):
        if (not sample.source.strip() or not sample.source_group.strip()
                or any(x.strip().upper().startswith('TODO') for x in (sample.source, sample.source_group))
                or sample.expected_decision == 'fail' and not sample.expected_issue_codes):
            raise InputError('archived sample source or defect labels are incomplete')
        expected_input = f'samples/{number:04d}/input.json'
        if sample.input_path != expected_input:
            raise InputError('archived sample input layout differs')
        local = _parse(content.get(expected_input, b''), LocalManifest)
        image_path = _reference(root / f'samples/{number:04d}', local.image.path)
        image_name = image_path.relative_to(root).as_posix()
        audit = load_input(root / expected_input)
        if audit.sample_id != sample.sample_id:
            raise InputError('archived audit sample identity differs')
        audits[sample.sample_id] = audit
        full_bindings.append(QualitySampleBindingV1(sample_id=sample.sample_id,
            input_sha256=input_fingerprint(audit), image_sha256=audit.image.sha256))
        expected_kinds.update({expected_input: 'input', image_name: 'image'})
        if sample.response_path is not None:
            if sample.response_path != f'samples/{number:04d}/observation.json':
                raise InputError('archived observation layout differs')
            content[sample.response_path].decode('utf-8')
            expected_kinds[sample.response_path] = 'observation'
    validate_labels(labels, audits)
    by_sample = {sample.sample_id: sample for sample in dataset.samples}
    for label in labels.items:
        sample = by_sample[label.sample_id]
        expected = (sample.expected_decision, tuple(sorted(set(sample.expected_issue_codes))),
            tuple(sorted(set(sample.allowed_repair_stages))) if sample.allowed_repair_stages is not None else None)
        if label_value(label) != expected:
            raise InputError('archived dataset differs from independent label sheet')
    selected = {s.sample_id for s in dataset.samples if s.split == 'validation'}
    bindings = tuple(b for b in full_bindings if b.sample_id in selected)
    if (dataset.purpose != manifest.source_purpose or labels.annotator != manifest.annotator
            or bindings != manifest.samples or thresholds.validation_samples != len(selected)
            or not any(by_sample[s].expected_decision == 'pass' for s in selected)
            or not any(by_sample[s].expected_decision == 'fail' for s in selected)):
        raise InputError('archived validation design differs')
    image_splits = {}
    for sample in dataset.samples:
        sha = audits[sample.sample_id].image.sha256
        if image_splits.setdefault(sha, sample.split) != sample.split:
            raise InputError('archived image crosses development and validation splits')
    secondary = None
    if manifest.validation_path == 'primary_plus_local_ocr':
        tool = parse_frozen_quality_tool(content.get('tool_identity.json', b''))
        if (tool.tool_sha256 != manifest.secondary_tool_sha256
                or tool.secondary_policy_sha256 != manifest.secondary_policy_sha256):
            raise InputError('archived local tool identity differs')
        secondary = frozen_secondary_policy(tool)
        expected_kinds['tool_identity.json'] = 'tool_identity'
    if expected_kinds != {name: item.kind for name, item in inventory.items()}:
        raise InputError('archived quality artifact kinds differ')
    return manifest, dataset, labels, thresholds, config, full_bindings, secondary


def _run_spec(content, manifest, dataset, config, full_bindings, run):
    spec, plan = run['spec'], manifest.plan
    jobs = []
    binding = {b.sample_id: b for b in manifest.samples}
    for repeat in range(1, plan.repeats + 1):
        for sample in dataset.samples:
            if sample.split == 'validation':
                b = binding[sample.sample_id]
                jobs.append(dict(sample.model_dump(mode='json'), repeat=repeat,
                    image_sha256=b.image_sha256, input_sha256=b.input_sha256,
                    response_sha256=None if manifest.execution_mode == 'live' or sample.response_path is None
                    else _digest(content[sample.response_path])))
    expected = dict(input_hashes={b.sample_id:b.input_sha256 for b in full_bindings}, jobs=jobs,
        dataset_sha256=_digest(content['dataset.json']), split='validation', repeats=plan.repeats,
        max_total_attempts=plan.max_total_attempts, max_attempts_per_audit=plan.max_attempts_per_audit,
        request_timeout=plan.request_timeout, total_timeout=plan.total_timeout,
        software_sha256=manifest.software_fingerprint_v1, prompt_version=manifest.prompt_version,
        purpose=dataset.purpose, quality_protocol_version=2, policy_binding_sha256=_quality_policy_hash(manifest),
        experiment_identity_sha256=manifest.experiment_identity.identity_sha256,
        validation_path=manifest.validation_path, execution_mode=manifest.execution_mode,
        claim_scope='selected_audit_path_only', live=manifest.execution_mode == 'live')
    if expected['live']:
        expected.update(provider_config=config.model_dump(mode='json'), provider=f'{config.profile}/{config.model}',
            evidence_kind='live_model_evaluation' if dataset.purpose == 'human_labeled' else 'software_demo_only')
    else:
        expected.update(provider_config=None, provider='fake-fixed-v1')
    if (type(spec.get('live')) is not bool or any(type(spec.get(k)) is not int
            for k in ('repeats','max_total_attempts','max_attempts_per_audit','quality_protocol_version'))
            or any(spec.get(k) != v for k,v in expected.items())
            or spec.get('client_policy','per_request') != _manifest_client_policy(manifest)):
        raise InputError('archived run differs from the complete frozen plan')
    return jobs


def verify_archived_quality(bundle_root, run_root, receipt_path, execution_binding_path) -> VerifiedQualityArchive:
    """Recompute a saved V2 receipt without current source/tool probes or writes."""
    try:
        root, out = _safe_path(Path(bundle_root), file=False), _safe_path(Path(run_root), file=False)
        if out.is_relative_to(root) or root.is_relative_to(out):
            raise InputError('archived run and frozen bundle must be separate')
        receipt_path, execution_path = _safe_path(Path(receipt_path)), _safe_path(Path(execution_binding_path))
        start_path = _reference(execution_path.parent, 'quality_start.json')
        content, run_content = _snapshot(root), _snapshot(out)
        receipt_raw, execution_raw, start_raw = map(_read_regular, (receipt_path, execution_path, start_path))
        manifest, dataset, labels, threshold, config, bindings, secondary = _bundle(root, content)
        saved = _parse(receipt_raw, QualityReceiptV2)
        execution, start = _parse(execution_raw, QualityExecutionV2), _parse(start_raw, QualityStartV2)
        run = read_run(out)
        jobs = _run_spec(content, manifest, dataset, config, bindings, run)
        rows, calls = read_results(out, run, recover=False)
        reports, case_calls = {}, {}
        for index, row in rows.items():
            raw = run_content.get(f'{index:04d}.json')
            if raw is None:
                reports[index], case_calls[index] = None, ()
                continue
            artifact = _decode_json(raw)
            case_calls[index] = artifact_calls(artifact, jobs[index], run['spec'])
            report = None if artifact.get('kind') == 'execution_error' else parse_report(raw)
            if report is not None:
                audit = load_input(_reference(root,jobs[index]['input_path']))
                report = _bound_report(report,audit,model=run['spec']['provider'],live=run['spec']['live'])
            reports[index] = report
            if report is not None and (report.schema_version != 3 or report.input_fingerprint != jobs[index]['input_sha256']
                    or report.prompt_version != manifest.prompt_version or report.execution_mode != manifest.execution_mode):
                raise InputError('archived report input, prompt or mode differs')
            if report is not None and report.decision == 'pass' and manifest.execution_mode == 'live' and not any(
                    c.live and c.status == 'ok' and c.transport_status == 'succeeded'
                    and c.validation_status == 'valid' and c.http_status == 200 for c in report.calls):
                raise InputError('archived live pass lacks a validated service response')
        local_used, local_starts, local_unresolved = _verify_v2_run_evidence(root,out,manifest,run,rows,secondary)
        artifacts = _run_artifacts(out,jobs,manifest=manifest)
        if (execution.bundle_seal_sha256 != manifest.seal_sha256 or execution.run_fingerprint != run['fingerprint']
                or execution.run_artifacts_sha256 != _artifacts_hash(artifacts)
                or execution.actual_calls_sha256 != fingerprint([c.model_dump(mode='json') for c in calls])
                or execution.start_sha256 != _digest(start_raw) or start.bundle_seal_sha256 != manifest.seal_sha256
                or start.plan != manifest.plan or start.evidence_kind != execution.evidence_kind
                or start.policy_binding_sha256 != _quality_policy_hash(manifest)
                or execution.policy_binding_sha256 != _quality_policy_hash(manifest)
                or (execution.evidence_kind == 'fake_replay') != (manifest.execution_mode == 'fake')):
            raise InputError('archived execution provenance binding differs')
        for name, raw in (('quality_execution.json',execution_raw),('quality_start.json',start_raw)):
            artifacts.append(QualityArtifactV1(path=name,sha256=_digest(raw),size_bytes=len(raw),kind='observation'))
        complete_count = sum(f'{i:04d}.result.json' in run_content for i in range(len(jobs)))
        complete = len(rows) == len(jobs) and complete_count == len(jobs)
        reasons = []
        evidence = execution.evidence_kind
        if evidence != 'live_service': reasons.append('real_service_provenance_missing')
        if manifest.source_purpose != 'human_labeled' or manifest.label_kind != 'independent_human':
            reasons.append('independent_human_labels_missing')
        if manifest.threshold_state != 'agreed': reasons.append('thresholds_not_agreed')
        if local_unresolved: reasons.append('local_tool_accounting_incomplete')
        if not complete: reasons.append('planned_results_incomplete')
        if evidence == 'live_service':
            if not calls or not all(c.live for c in calls): reasons.append('actual_live_calls_missing')
            if any(c.validation_status == 'pending' for c in calls): reasons.append('call_validation_incomplete')
            if any(r['decision'] is not None and not r['attempts'] for r in rows.values()): reasons.append('report_live_call_missing')
        metrics = planned_quality_metrics(jobs,rows)
        per_repeat = {}
        for repeat in range(1,manifest.plan.repeats+1):
            selected = [i for i, job in enumerate(jobs) if job['repeat'] == repeat]
            per_repeat[str(repeat)] = planned_quality_metrics([jobs[i] for i in selected],
                {position:rows[i] for position,i in enumerate(selected) if i in rows})
        gates = evaluate_quality_gates(threshold,metrics,per_repeat)
        usage = usage_summary(calls)
        usage.update(quality_is_not_usage_or_invoice=True,
            usage_completeness='no_service_calls' if not usage['live_attempts'] else
                'incomplete' if usage['attempts_with_unknown_usage'] else 'complete',
            service_response_models=sorted({c.response_model for c in calls if c.live and c.response_model}),
            service_model_identity_missing_attempts=sum(c.live and not c.response_model for c in calls), model_attempts=len(calls))
        body = dict(schema_version=2,kind='a4_quality_receipt',bundle_seal_sha256=manifest.seal_sha256,
            run_fingerprint=run['fingerprint'],evidence_kind=evidence,status='complete' if complete else 'incomplete',
            quality_acceptance='not_assessed' if reasons else 'passed' if all(g['passed'] for g in gates) else 'failed',
            reason_codes=reasons,planned_audits=len(jobs),recorded_audits=len(rows),completed_result_files=complete_count,
            missing_audits=len(jobs)-len(rows),execution_errors=sum(r['decision'] is None for r in rows.values()),
            unknown_decisions=sum(r['decision']=='unknown' for r in rows.values()),metrics=metrics,per_repeat=per_repeat,
            quality_gates=list(gates),usage=usage,artifacts=[a.model_dump(mode='json') for a in sorted(artifacts,key=lambda a:a.path)],
            validation_path=manifest.validation_path,execution_mode=manifest.execution_mode,
            experiment_identity_sha256=manifest.experiment_identity.identity_sha256,capability_sha256=manifest.capability_sha256,
            language_profile_sha256=manifest.language_profile_sha256,secondary_policy_sha256=manifest.secondary_policy_sha256,
            secondary_tool_sha256=manifest.secondary_tool_sha256,local_tool_attempts=local_used if not local_unresolved else None,
            local_tool_process_starts=local_starts if not local_unresolved else None,local_tool_known_attempts=local_used,
            local_tool_known_process_starts=local_starts,local_tool_unresolved_invocations=local_unresolved,
            claim_scope='selected_audit_path_only')
        recomputed = _parse(_json_bytes(dict(body,receipt_sha256=fingerprint(body))),QualityReceiptV2)
        if recomputed != saved:
            raise InputError('archived quality receipt differs from its full evidence')
        if (_snapshot(root) != content or _snapshot(out) != run_content
                or any(_read_regular(p) != raw for p,raw in ((receipt_path,receipt_raw),(execution_path,execution_raw),(start_path,start_raw)))):
            raise InputError('quality archive changed during verification')
        return VerifiedQualityArchive(manifest,saved,dataset,labels,threshold,tuple(jobs),rows,reports,calls,case_calls,
            {a.path:a.sha256 for a in artifacts},_digest(content['manifest.json']),_digest(receipt_raw),_digest(execution_raw),
            None if 'agreement.bin' not in content else _digest(content['agreement.bin']))
    except (KeyError, ValueError, TypeError, AttributeError, UnicodeError, OSError):
        raise InputError('invalid or inconsistent archived quality evidence') from None

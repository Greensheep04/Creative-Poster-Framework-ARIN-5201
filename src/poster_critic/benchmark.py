"""Read-only audit-only comparisons of existing, fully bound telemetry.

No model, network, dispatch, retry, or command execution occurs here. Missing
or rejected cases remain in planned denominators. Quality acceptance requires
more than telemetry decisions; optional archive connections independently
recheck all formal records before a quality conclusion.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from math import ceil, isfinite
import os
from pathlib import Path, PurePosixPath
import random
import re
import stat
from statistics import median
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from .adapters.local_input import load_input
from .contracts import Contract, Text
from .data import LabelSheet
from .errors import InputError
from .evaluate import Dataset
from .experiment_identity import ExperimentIdentityV1
from .evidence import Digest
from .identity import fingerprint, input_fingerprint
from .provider_capabilities import ProviderCapabilityV1, capability_fingerprint, load_capability
from .reporting import write_bytes, write_json
from .telemetry import BenchmarkCaseV1, MeasurementKind, TelemetryEnvelopeV1

_MAX_FILE = 4 * 1024 * 1024
_MAX_TOTAL = 64 * 1024 * 1024
_MAX_CASES = 10000


class _Versioned(Contract):
    schema_version: Literal[1] = 1

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int:
            raise ValueError("benchmark versions must be integers")
        return value


class PriceProfileV1(_Versioned):
    price_id: Text
    provider_profile: Literal["qwen", "glm", "minimax", "compatible"]
    model_id: Text
    endpoint_family: Text
    region: Text | None
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    input_per_million: Annotated[float, Field(ge=0)]
    output_per_million: Annotated[float, Field(ge=0)]
    effective_at: Text
    source_url: Text
    price_sha256: Digest

    @model_validator(mode="after")
    def valid_price(self) -> Self:
        _utc(self.effective_at)
        url = urlsplit(self.source_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("price sources must be explicit safe HTTPS references")
        if self.price_sha256 != fingerprint(self.model_dump(mode="json", exclude={"price_sha256"})):
            raise ValueError("price profile content differs from its digest")
        return self


class SampleBindingV1(Contract):
    sample_id: Text
    input_fingerprint: Digest
    image_sha256: Digest


class ArmBudgetsV1(Contract):
    baseline: Annotated[int, Field(ge=1)]
    candidate: Annotated[int, Field(ge=1)]


class QualityGateBindingV1(Contract):
    state: Literal["not_agreed", "agreed"]
    threshold_profile_sha256: Digest | None

    @model_validator(mode="after")
    def actual_reference(self) -> Self:
        if (self.state == "agreed") != (self.threshold_profile_sha256 is not None):
            raise ValueError("agreed gates require an explicit frozen threshold reference")
        return self


class BenchmarkSpecV1(_Versioned):
    kind: Literal["a4_benchmark_spec"] = "a4_benchmark_spec"
    experiment_id: Text
    change_id: Text
    scope: Literal["audit_only"] = "audit_only"
    dataset_sha256: Digest
    labels_sha256: Digest | None
    baseline_identity_sha256: Digest
    candidate_identity_sha256: Digest
    measurement_kind: MeasurementKind
    sample_ids: Annotated[tuple[Text, ...], Field(min_length=1, max_length=1000)]
    sample_bindings: tuple[SampleBindingV1, ...]
    repetitions: Annotated[int, Field(ge=1, le=1000)]
    warmup_repetitions: Annotated[int, Field(ge=0, le=1000)]
    concurrency: Literal[1] = 1
    order: Literal["baseline_then_candidate", "candidate_then_baseline"]
    order_seed: Annotated[int, Field(ge=0, le=2**63 - 1)]
    attempt_budget: Annotated[int, Field(ge=1)]
    arm_attempt_budgets: ArmBudgetsV1
    price_profile: PriceProfileV1 | None
    quality_gate: QualityGateBindingV1
    spec_sha256: Digest

    @field_validator("concurrency", mode="before")
    @classmethod
    def exact_concurrency(cls, value):
        if type(value) is not int:
            raise ValueError("benchmark concurrency must be an integer")
        return value

    @model_validator(mode="after")
    def coherent_plan(self) -> Self:
        if len(set(self.sample_ids)) != len(self.sample_ids):
            raise ValueError("benchmark sample IDs must be unique")
        if [x.sample_id for x in self.sample_bindings] != list(self.sample_ids):
            raise ValueError("benchmark bindings must cover the declared samples in order")
        if self.arm_attempt_budgets.baseline + self.arm_attempt_budgets.candidate != self.attempt_budget:
            raise ValueError("benchmark arm budgets must sum to the fixed total")
        if 2 * len(self.sample_ids) * (self.repetitions + self.warmup_repetitions) > _MAX_CASES:
            raise ValueError("benchmark planned case count exceeds the fixed bound")
        if self.spec_sha256 != fingerprint(self.model_dump(mode="json", exclude={"spec_sha256"})):
            raise ValueError("benchmark plan content differs from its digest")
        return self


class BenchmarkNotStartedV1(_Versioned):
    kind: Literal["a4_benchmark_not_started"] = "a4_benchmark_not_started"
    benchmark_case: BenchmarkCaseV1
    reason_code: Literal["arm_budget_exhausted", "external_measurement_stopped", "setup_blocked"]


class BenchmarkRecordV1(Contract):
    benchmark_case: BenchmarkCaseV1 | None
    source_arm: Literal["baseline", "candidate"] | None
    status: Literal["recorded", "not_started", "missing", "duplicate", "rejected"]
    telemetry_path: str | None
    telemetry_sha256: Digest | None
    source_record_sha256: Digest | None
    reason_code: Text | None

    @model_validator(mode="after")
    def safe_reference(self) -> Self:
        if self.telemetry_path is not None:
            _relative(self.telemetry_path)
        if (self.telemetry_path is None) != (self.telemetry_sha256 is None):
            raise ValueError("record output references require their actual bytes digest")
        return self


class BenchmarkSummaryV1(_Versioned):
    kind: Literal["a4_benchmark_summary"] = "a4_benchmark_summary"
    spec_sha256: Digest
    status: Literal["complete", "incomplete", "rejected"]
    cases: tuple[BenchmarkRecordV1, ...]
    metrics: dict
    quality_acceptance: Literal["not_assessed", "passed", "failed"]
    quality_reason_code: Text


def _utc(value: str) -> None:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset().total_seconds() != 0:
        raise ValueError("benchmark timestamps require UTC")


def _relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or "\\" in value or str(path) != value or any(
        part in (".", "..") or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", part) is None for part in path.parts
    ):
        raise ValueError("benchmark references must be controlled relative paths")
    return path


def _read(path: Path) -> bytes:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise InputError("benchmark inputs must not follow symlinks")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_FILE:
                raise ValueError
            raw = file.read(_MAX_FILE + 1)
            after = os.fstat(file.fileno())
        if len(raw) > _MAX_FILE or (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError
        return raw
    except (OSError, ValueError):
        raise InputError("benchmark input is missing, nonregular, changing or oversized") from None


def _json(raw: bytes) -> dict:
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    def reject(value):
        raise ValueError
    result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject)
    if not isinstance(result, dict):
        raise ValueError
    return result


def _construct(model, hash_field, fields):
    # Normalize typed nested objects and defaults before sealing, without
    # trusting model_construct/model_copy supplied by an external caller.
    serialized = json.loads(json.dumps(fields, default=lambda x: x.model_dump(mode="json"), allow_nan=False))
    serialized.setdefault("schema_version", 1)
    if model is BenchmarkSpecV1:
        serialized.setdefault("kind", "a4_benchmark_spec")
        serialized.setdefault("scope", "audit_only")
        serialized.setdefault("concurrency", 1)
    serialized.pop(hash_field, None)
    return model.model_validate_json(json.dumps({**serialized, hash_field: fingerprint(serialized)}, allow_nan=False))


def make_price_profile(**fields) -> PriceProfileV1:
    try:
        for name in ("input_per_million", "output_per_million"):
            if type(fields.get(name)) not in (int, float):
                raise ValueError
            fields[name] = float(fields[name])
        return _construct(PriceProfileV1, "price_sha256", fields)
    except (ValueError, TypeError, AttributeError):
        raise InputError("invalid explicit flat-rate price profile") from None


def make_benchmark_spec(**fields) -> BenchmarkSpecV1:
    try:
        return _construct(BenchmarkSpecV1, "spec_sha256", fields)
    except (ValueError, TypeError, AttributeError):
        raise InputError("invalid fixed audit-only benchmark specification") from None


def _checked_spec(spec: BenchmarkSpecV1) -> BenchmarkSpecV1:
    try:
        if not isinstance(spec, BenchmarkSpecV1):
            raise ValueError
        return BenchmarkSpecV1.model_validate_json(spec.model_dump_json())
    except (ValueError, TypeError, AttributeError):
        raise InputError("invalid sealed benchmark specification") from None


def _make_case_checked(spec: BenchmarkSpecV1, *, arm: Literal["baseline", "candidate"],
                        sample_id: str, repetition: int, warmup: bool = False) -> BenchmarkCaseV1:
    if sample_id not in spec.sample_ids or type(warmup) is not bool or type(repetition) is not int or not (
        1 <= repetition <= (spec.warmup_repetitions if warmup else spec.repetitions)
    ):
        raise InputError("benchmark case is outside its fixed plan")
    fields = dict(spec_sha256=spec.spec_sha256, arm=arm, sample_id=sample_id, repetition=repetition, warmup=warmup)
    try:
        return BenchmarkCaseV1(**fields, case_id=fingerprint(fields))
    except (ValueError, TypeError):
        raise InputError("invalid benchmark arm or case identity") from None


def make_benchmark_case(spec: BenchmarkSpecV1, *, arm: Literal["baseline", "candidate"],
                        sample_id: str, repetition: int, warmup: bool = False) -> BenchmarkCaseV1:
    return _make_case_checked(_checked_spec(spec), arm=arm, sample_id=sample_id, repetition=repetition, warmup=warmup)


def planned_cases(spec: BenchmarkSpecV1) -> tuple[BenchmarkCaseV1, ...]:
    spec = _checked_spec(spec)
    arms = ("baseline", "candidate") if spec.order == "baseline_then_candidate" else ("candidate", "baseline")
    rng = random.Random(spec.order_seed)
    result = []
    for arm in arms:
        samples = list(spec.sample_ids)
        rng.shuffle(samples)
        for warmup, repeats in ((True, spec.warmup_repetitions), (False, spec.repetitions)):
            for repetition in range(1, repeats + 1):
                result.extend(_make_case_checked(spec, arm=arm, sample_id=sample, repetition=repetition, warmup=warmup) for sample in samples)
    return tuple(result)


def _dataset(spec, dataset_path, labels_path):
    try:
        packet = _json(_read(Path(dataset_path)))
        if type(packet.get("schema_version")) is not int:
            raise ValueError
        dataset = Dataset.model_validate_json(json.dumps(packet))
        if fingerprint(dataset.model_dump(mode="json")) != spec.dataset_sha256 or tuple(x.sample_id for x in dataset.samples) != spec.sample_ids:
            raise ValueError
        expected = {x.sample_id: x for x in spec.sample_bindings}
        for sample in dataset.samples:
            audit = load_input(Path(dataset_path).parent / sample.input_path)
            binding = expected[sample.sample_id]
            if audit.sample_id != sample.sample_id or input_fingerprint(audit) != binding.input_fingerprint or audit.image.sha256 != binding.image_sha256:
                raise ValueError
        labels = None
        if labels_path is not None:
            label_packet = _json(_read(Path(labels_path)))
            if type(label_packet.get("schema_version")) is not int:
                raise ValueError
            labels = LabelSheet.model_validate_json(json.dumps(label_packet))
            if spec.labels_sha256 is None or fingerprint(labels.model_dump(mode="json")) != spec.labels_sha256:
                raise ValueError
            if {x.sample_id for x in labels.items} != set(spec.sample_ids):
                raise ValueError
            for label in labels.items:
                binding = expected[label.sample_id]
                sample = next(x for x in dataset.samples if x.sample_id == label.sample_id)
                if (label.input_sha256 != binding.input_fingerprint or label.image_sha256 != binding.image_sha256
                        or (label.decision is not None and label.decision != sample.expected_decision)
                        or (label.decision == "fail" and set(label.issue_codes) != set(sample.expected_issue_codes))):
                    raise ValueError
        return dataset, labels
    except (ValueError, TypeError, UnicodeError):
        raise InputError("benchmark dataset, labels or current image/input bindings differ from the fixed plan") from None


def _records(directory: Path, arm: str):
    if not directory.is_dir() or any(p.is_symlink() for p in (directory, *directory.parents)):
        raise InputError("benchmark arm must be an existing regular measurement directory")
    result = []
    total = 0
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise InputError("benchmark arm must not contain symlinks")
        if path.is_dir():
            continue
        relative = path.relative_to(directory).as_posix()
        try:
            _relative(relative)
        except ValueError:
            raise InputError("benchmark measurement references are not portable") from None
        if path.suffix != ".json":
            raise InputError("benchmark arm contains uncontrolled non-JSON state")
        raw = _read(path)
        total += len(raw)
        if total > _MAX_TOTAL or len(result) >= _MAX_CASES:
            raise InputError("benchmark record inventory exceeds its fixed limits")
        parsed, error = None, None
        try:
            packet = _json(raw)
            model = BenchmarkNotStartedV1 if packet.get("kind") == "a4_benchmark_not_started" else TelemetryEnvelopeV1
            parsed = model.model_validate_json(json.dumps(packet))
        except (ValueError, TypeError, UnicodeError):
            error = "telemetry_invalid"
        result.append({"arm": arm, "relative": relative, "raw": raw, "sha256": hashlib.sha256(raw).hexdigest(),
                       "value": parsed, "error": error, "case": parsed.benchmark_case if parsed is not None else None})
    return result


def _latency(values):
    values = sorted(values)
    return {"sample_count": len(values), "median": median(values) if values else None,
            "p95_nearest_rank": values[ceil(.95 * len(values)) - 1] if values else None,
            "small_sample": len(values) < 20}


def _usage(envelopes, price, capabilities, *, unknown_records, accounting_uncertain):
    calls = [call for value in envelopes for call in value.calls if call.live]
    known = [call for call in calls if call.usage is not None]
    totals = {"reported_input_tokens": sum(c.usage.input_tokens for c in known),
              "reported_output_tokens": sum(c.usage.output_tokens for c in known),
              "attempts_with_unknown_usage": len(calls) - len(known),
              "attempts_with_usage_details": sum(bool(c.usage_details) for c in calls),
              "unparsed_records": unknown_records,
              "accounting_uncertain": accounting_uncertain,
              "currency": price.currency if price else None, "known_usage_cost_estimate": None,
              "total_cost_estimate": None, "price_status": "not_provided" if price is None else "unverified"}
    if price is None:
        return totals
    cap_map = {capability_fingerprint(x): x for x in capabilities}
    for envelope in envelopes:
        if not any(c.live for c in envelope.calls):
            continue
        if datetime.fromisoformat(envelope.started_at.replace("Z", "+00:00")) < datetime.fromisoformat(price.effective_at.replace("Z", "+00:00")):
            totals["price_status"] = "not_effective_at_measurement"
            return totals
        identity = envelope.experiment_identity or {}
        capability = cap_map.get(identity.get("capability_sha256"))
        if capability is None or (capability.provider_profile, capability.model_id, capability.endpoint_family, capability.region) != (
            price.provider_profile, price.model_id, price.endpoint_family, price.region
        ) or any(c.model != f"{capability.provider_profile}/{capability.model_id}"
                 or (c.response_model is not None and c.response_model != capability.model_id)
                 for c in envelope.calls if c.live):
            totals["price_status"] = "profile_mismatch_or_unverified"
            return totals
    totals["price_status"] = "matched_explicit_flat_rate"
    try:
        subtotal = (totals["reported_input_tokens"] * price.input_per_million + totals["reported_output_tokens"] * price.output_per_million) / 1_000_000
        if not isfinite(subtotal):
            raise OverflowError
    except OverflowError:
        totals["price_status"] = "estimate_out_of_range"
        return totals
    totals["known_usage_cost_estimate"] = subtotal
    if not totals["attempts_with_unknown_usage"] and not accounting_uncertain:
        totals["total_cost_estimate"] = subtotal
    return totals


def _counts(planned, envelopes, rows, *, comparable_envelopes, labels, price, capabilities,
            unknown_records, accounting_uncertain, warmup_statistics=False):
    calls = [call for value in envelopes for call in value.calls]
    completed = [x for x in comparable_envelopes if x.terminal_outcome == "completed" and x.audit_decision is not None]
    interrupted = [x for x in comparable_envelopes if x not in completed]
    latency_completed = [x for x in completed if warmup_statistics or (x.benchmark_case is not None and not x.benchmark_case.warmup)]
    latency_interrupted = [x for x in interrupted if warmup_statistics or (x.benchmark_case is not None and not x.benchmark_case.warmup)]
    status_counts = {state: sum(row.status == state for row in rows) for state in ("recorded", "missing", "not_started", "duplicate", "rejected")}
    result = {"planned": len(planned), "attempted": len(comparable_envelopes), "observed_records": len(envelopes), "audit_completed": len(completed),
        "execution_error": sum(x.terminal_outcome == "execution_error" for x in comparable_envelopes),
        "input_error": sum(x.terminal_outcome == "input_error" for x in comparable_envelopes),
        "delivery_error": sum(x.terminal_outcome == "delivery_error" for x in comparable_envelopes),
        "cancelled": sum(x.terminal_outcome == "cancelled" for x in comparable_envelopes),
        "unknown": sum(x.audit_decision == "unknown" for x in completed),
        "delivery": "not_assessed", "case_statuses": status_counts,
        "actual_attempts": None if accounting_uncertain else len(calls), "known_recorded_attempts": len(calls),
        "accounting_uncertain": accounting_uncertain,
        "retries": sum(max(0, len(x.calls) - 1) for x in envelopes),
        "recorded_rejected_admission": sum(stage.name == "budget_admission" and stage.outcome == "rejected" for x in envelopes for stage in x.stages),
        "quality_latency_scope": "warmup_only" if warmup_statistics else "formal_only",
        "latency_ms": {"completed_audit": _latency([x.elapsed_total_ms for x in latency_completed]),
                       "interrupted_audit": _latency([x.elapsed_total_ms for x in latency_interrupted])},
        "usage": _usage(envelopes, price, capabilities, unknown_records=unknown_records, accounting_uncertain=accounting_uncertain)}
    by_case = {row.benchmark_case.case_id: row for row in rows if row.benchmark_case is not None}
    valid_envelopes = {x.benchmark_case.case_id: x for x in comparable_envelopes if x.benchmark_case is not None}
    label_map = {x.sample_id: x for x in labels.items} if labels else {}
    quality = {"unlabeled_or_incomplete_planned": 0, "normal": {"planned": 0, "false_fail": 0, "unknown": 0, "error": 0, "missing": 0},
               "problem": {"planned": 0, "false_pass": 0, "unknown": 0, "error": 0, "missing": 0}}
    for case in planned:
        if case.warmup and not warmup_statistics:
            continue
        label = label_map.get(case.sample_id)
        if label is None or label.decision not in ("pass", "fail") or not label.evidence.strip() or (label.decision == "fail" and not label.issue_codes):
            quality["unlabeled_or_incomplete_planned"] += 1
            continue
        group = quality["normal" if label.decision == "pass" else "problem"]
        group["planned"] += 1
        row = by_case[case.case_id]
        value = valid_envelopes.get(case.case_id) if row.status == "recorded" else None
        if value is None:
            group["missing"] += 1
        elif value.terminal_outcome != "completed" or value.audit_decision is None:
            group["error"] += 1
        elif value.audit_decision == "unknown":
            group["unknown"] += 1
        elif value.audit_decision != label.decision:
            group["false_fail" if label.decision == "pass" else "false_pass"] += 1
    for group, numerator in ((quality["normal"], "false_fail"), (quality["problem"], "false_pass")):
        group["rate"] = group[numerator] / group["planned"] if group["planned"] else None
    result["decision_label_comparison"] = quality
    return result


def _validate_threshold_evidence(spec, threshold_profile_path, agreement_path):
    if threshold_profile_path is None:
        if agreement_path is not None:
            raise InputError("benchmark agreement evidence requires its threshold profile")
        return
    try:
        from .quality_validation import ThresholdProfileV1
        threshold = ThresholdProfileV1.model_validate_json(_read(Path(threshold_profile_path)))
        if fingerprint(threshold.model_dump(mode="json")) != spec.quality_gate.threshold_profile_sha256 or threshold.state != "agreed":
            raise InputError("benchmark threshold evidence differs from its declared gate")
        if agreement_path is None or hashlib.sha256(_read(Path(agreement_path))).hexdigest() != threshold.agreement_evidence_sha256:
            raise InputError("benchmark threshold agreement evidence is missing or different")
    except (ValueError, TypeError, UnicodeError):
        raise InputError("benchmark threshold or agreement evidence is invalid") from None


def summarize_benchmark(spec: BenchmarkSpecV1, dataset_path: str | Path, *,
                        baseline_dir: str | Path, candidate_dir: str | Path, out: str | Path,
                        labels_path: str | Path | None = None,
                        capabilities: tuple[ProviderCapabilityV1, ...] = (),
                        threshold_profile_path: str | Path | None = None,
                        agreement_path: str | Path | None = None,
                        quality_link_path: str | Path | None = None) -> BenchmarkSummaryV1:
    """Validate existing records and publish into a new directory only.

    Invalid raw records remain untouched in the input directory. Output stores
    a sanitized hash/path reference instead of exporting unvalidated bodies.
    Historical arm identities are checked internally, not restamped to the
    currently running source or environment.
    """
    spec = _checked_spec(spec)
    output = Path(out).expanduser().absolute()
    if output.exists() or any(p.is_symlink() for p in (output, *output.parents)):
        raise InputError("benchmark output must be a new nonsymlink directory")
    for directory in (Path(baseline_dir).expanduser().absolute(), Path(candidate_dir).expanduser().absolute()):
        if output.is_relative_to(directory):
            raise InputError("benchmark output must be outside the read-only measurement directories")
    dataset, labels = _dataset(spec, Path(dataset_path), labels_path)
    for capability in capabilities:
        capability_fingerprint(capability)
    _validate_threshold_evidence(spec, threshold_profile_path, agreement_path)
    cases = planned_cases(spec)
    expected = {x.case_id: x for x in cases}
    loaded = _records(Path(baseline_dir), "baseline") + _records(Path(candidate_dir), "candidate")
    grouped = {x.case_id: [] for x in cases}
    identities = {"baseline": spec.baseline_identity_sha256, "candidate": spec.candidate_identity_sha256}
    bindings = {x.sample_id: x for x in spec.sample_bindings}
    measurement_ids = {}
    for record in loaded:
        value, case = record["value"], record["case"]
        if record["error"] is None:
            if case is None or case.case_id not in expected or case != expected[case.case_id] or case.arm != record["arm"]:
                record["error"] = "case_not_in_plan"
            elif isinstance(value, TelemetryEnvelopeV1):
                identity = value.experiment_identity
                binding = bindings[case.sample_id]
                if value.scope != "audit_only" or value.delivery_state != "not_called" or any(x.name == "delivery" and x.outcome != "not_run" for x in value.stages) or value.terminal_outcome == "delivery_error":
                    record["error"] = "audit_only_side_effects"
                elif value.measurement_status != "recorded" or value.measurement_kind != spec.measurement_kind:
                    record["error"] = "measurement_kind_or_status_mismatch"
                elif identity is None or identity["identity_sha256"] != identities[case.arm]:
                    record["error"] = "experiment_identity_mismatch"
                elif (value.input_fingerprint != binding.input_fingerprint or value.image_sha256 != binding.image_sha256) and not (
                    value.terminal_outcome == "input_error" and value.audit_decision is None and not value.calls
                    and value.input_fingerprint is None and value.image_sha256 is None
                ):
                    record["error"] = "sample_binding_mismatch"
                elif spec.measurement_kind == "live_service" and (identity["provider_config_sha256"] is None or identity["capability_sha256"] is None):
                    record["error"] = "live_capability_binding_missing"
                elif [x.attempt for x in value.calls] != list(range(1, len(value.calls) + 1)) or len(value.calls) > 2:
                    record["error"] = "call_budget_protocol_mismatch"
                elif any((call.transport_status, call.validation_status) not in (
                    (("succeeded", "pending"), ("succeeded", "valid")) if call.status == "ok" else
                    (("succeeded", "invalid"),) if call.status == "invalid_response" else (("failed", "not_run"),)
                ) for call in value.calls):
                    record["error"] = "call_record_state_mismatch"
                elif value.audit_decision == "pass" and not any(x.status == "ok" and x.validation_status == "valid" for x in value.calls):
                    record["error"] = "pass_observation_missing"
                measurement_ids.setdefault(value.measurement_id, []).append(record)
        if case is not None and case.case_id in grouped and case.arm == record["arm"]:
            grouped[case.case_id].append(record)
    for values in measurement_ids.values():
        if len(values) > 1:
            for record in values:
                record["error"] = record["error"] or "measurement_id_duplicate"
    # Count all distinct declared invocations, including rejected/extra cases.
    # Duplicate copies of one ID are not additional physical request evidence.
    envelopes_by_id = {}
    unparsed = {arm: 0 for arm in ("baseline", "candidate")}
    for record in loaded:
        if isinstance(record["value"], TelemetryEnvelopeV1):
            key = (record["arm"], record["value"].measurement_id)
            previous = envelopes_by_id.get(key)
            if previous is not None and previous.model_dump(mode="json") != record["value"].model_dump(mode="json"):
                unparsed[record["arm"]] += 1
            if previous is None or len(record["value"].calls) > len(previous.calls):
                envelopes_by_id[key] = record["value"]
        elif record["value"] is None:
            unparsed[record["arm"]] += 1
    exceeded = set()
    for arm in ("baseline", "candidate"):
        attempts = sum(len(v.calls) for (which, _), v in envelopes_by_id.items() if which == arm)
        if attempts > getattr(spec.arm_attempt_budgets, arm):
            exceeded.add(arm)
            for record in loaded:
                if record["arm"] == arm:
                    record["error"] = "arm_attempt_budget_exceeded"
    rows = []
    for case in cases:
        values = grouped[case.case_id]
        if not values:
            rows.append(BenchmarkRecordV1(benchmark_case=case, source_arm=None, status="missing", telemetry_path=None,
                telemetry_sha256=None, source_record_sha256=None, reason_code="planned_case_missing"))
        elif len(values) > 1:
            for record in values:
                record["status"] = "duplicate"
                record["error"] = record["error"] or "case_duplicate"
        else:
            record = values[0]
            record["status"] = "rejected" if record["error"] else "not_started" if isinstance(record["value"], BenchmarkNotStartedV1) else "recorded"
    for record in loaded:
        record.setdefault("status", "rejected")
    if any(x["status"] in ("duplicate", "rejected") for x in loaded) or exceeded:
        status = "rejected"
    elif any(x.status == "missing" for x in rows) or any(x["status"] == "not_started" for x in loaded):
        status = "incomplete"
    else:
        status = "complete"
    quality = None
    if quality_link_path is not None:
        from .benchmark_quality import verify_benchmark_quality
        quality = verify_benchmark_quality(spec, link_path=quality_link_path, loaded_records=loaded,
            dataset=dataset, labels=labels, benchmark_status=status)
        if quality.status == "rejected":
            status = "rejected"
    try:
        output.mkdir(parents=True, exist_ok=False)
        for index, record in enumerate(loaded):
            if record["value"] is None:
                relative = f"rejected/{record['arm']}/{index:04d}.json"
                wrapper = {"schema_version": 1, "kind": "a4_rejected_telemetry_reference",
                    "source_arm": record["arm"], "source_relative_path": record["relative"],
                    "source_record_sha256": record["sha256"], "reason_code": record["error"]}
                raw = (json.dumps(wrapper, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()
            else:
                relative = f"telemetry/{record['arm']}/{record['relative']}"
                raw = record["raw"]
            write_bytes(output / relative, raw)
            rows.append(BenchmarkRecordV1(benchmark_case=record["case"], source_arm=record["arm"], status=record["status"], telemetry_path=relative,
                telemetry_sha256=hashlib.sha256(raw).hexdigest(), source_record_sha256=record["sha256"], reason_code=record["error"]
                or (record["value"].reason_code if isinstance(record["value"], BenchmarkNotStartedV1) else None)))
        metrics = {}
        for arm in ("baseline", "candidate"):
            arm_cases = [x for x in cases if x.arm == arm]
            arm_rows = [x for x in rows if x.source_arm == arm or (x.source_arm is None and x.benchmark_case is not None and x.benchmark_case.arm == arm)]
            arm_envelopes = [v for (which, _), v in envelopes_by_id.items() if which == arm]
            comparable = [record["value"] for record in loaded if record["arm"] == arm
                          and record["status"] == "recorded" and isinstance(record["value"], TelemetryEnvelopeV1)]
            uncertain = bool(unparsed[arm]) or any(row.status in ("missing", "duplicate", "rejected") for row in arm_rows)
            metrics[arm] = {"attempt_budget": getattr(spec.arm_attempt_budgets, arm),
                "budget_exceeded": True if arm in exceeded else None if uncertain else False, "unparsed_records": unparsed[arm],
                "all": _counts(arm_cases, arm_envelopes, arm_rows, comparable_envelopes=comparable, labels=labels,
                    price=spec.price_profile, capabilities=capabilities, unknown_records=unparsed[arm], accounting_uncertain=uncertain),
                "formal": _counts([x for x in arm_cases if not x.warmup], [x for x in arm_envelopes if x.benchmark_case and not x.benchmark_case.warmup],
                    [x for x in arm_rows if x.benchmark_case is not None and not x.benchmark_case.warmup],
                    comparable_envelopes=[x for x in comparable if not x.benchmark_case.warmup], labels=labels,
                    price=spec.price_profile, capabilities=capabilities, unknown_records=unparsed[arm], accounting_uncertain=uncertain),
                "warmup": _counts([x for x in arm_cases if x.warmup], [x for x in arm_envelopes if x.benchmark_case and x.benchmark_case.warmup],
                    [x for x in arm_rows if x.benchmark_case is not None and x.benchmark_case.warmup],
                    comparable_envelopes=[x for x in comparable if x.benchmark_case.warmup], labels=None,
                    price=spec.price_profile, capabilities=capabilities, unknown_records=unparsed[arm], accounting_uncertain=uncertain, warmup_statistics=True)}
        reason = "not_live_service" if spec.measurement_kind != "live_service" else "full_report_quality_evidence_unverified"
        if labels is None or spec.labels_sha256 is None:
            reason = "independent_labels_missing"
        if spec.quality_gate.state != "agreed":
            reason = "quality_thresholds_not_agreed"
        metrics["measurement_kind"] = spec.measurement_kind
        metrics["quality_note"] = "Decision/label counts are descriptive. Telemetry lacks full-report issue evidence required by every quality gate; use separately verified frozen quality receipts."
        acceptance = "not_assessed"
        if quality is not None:
            metrics["quality_evidence"] = quality.model_dump(mode="json")
            metrics["quality_note"] = "Optional quality connection rechecks two historical frozen archives and every formal measurement. Warmup is excluded from quality. Timestamps bind protocol content and are not external audit authentication."
            acceptance = quality.quality_acceptance
            reason = quality.reason_codes[0] if quality.reason_codes else "two_arm_quality_gates_" + acceptance
            write_json(output / "quality_evidence.json", quality.model_dump(mode="json"))
        summary = BenchmarkSummaryV1(spec_sha256=spec.spec_sha256, status=status, cases=tuple(rows), metrics=metrics,
            quality_acceptance=acceptance, quality_reason_code=reason)
        write_json(output / "summary.json", summary.model_dump(mode="json"))
        return summary
    except (OSError, ValueError, TypeError):
        raise InputError("cannot publish complete benchmark summary; partial output must not be overwritten") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    summarize = sub.add_parser("summarize")
    for name in ("spec", "dataset", "baseline", "candidate", "out"):
        summarize.add_argument("--" + name, type=Path, required=True)
    summarize.add_argument("--labels", type=Path)
    summarize.add_argument("--capability", type=Path, action="append", default=[])
    summarize.add_argument("--thresholds", type=Path)
    summarize.add_argument("--agreement", type=Path)
    summarize.add_argument("--quality-link", type=Path)
    args = parser.parse_args(argv)
    try:
        spec = BenchmarkSpecV1.model_validate_json(_read(args.spec))
        result = summarize_benchmark(spec, args.dataset, baseline_dir=args.baseline, candidate_dir=args.candidate,
            out=args.out, labels_path=args.labels, capabilities=tuple(load_capability(x) for x in args.capability),
            threshold_profile_path=args.thresholds, agreement_path=args.agreement, quality_link_path=args.quality_link)
        print(f"{result.status}: {args.out.resolve() / 'summary.json'}; quality={result.quality_acceptance}")
        return 0 if result.status == "complete" else 2
    except (InputError, ValueError, OSError, TypeError):
        print("benchmark rejected: invalid evidence or output; no audit was executed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

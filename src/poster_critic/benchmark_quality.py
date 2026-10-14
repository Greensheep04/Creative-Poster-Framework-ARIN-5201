"""Join existing benchmark measurements to two independently verified archives.

The premeasurement plan seals known policies. The later link seals actual
record/report bytes. Neither document authenticates wall-clock time or runs
archived Python, a model, a command, a callback, or a network request.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from .benchmark import BenchmarkSpecV1, _checked_spec, _json, _read, _relative, planned_cases
from .contracts import Contract, Text
from .data import LabelSheet
from .errors import InputError
from .evaluate import Dataset
from .evidence import Digest
from .experiment_identity import ExperimentIdentityV1
from .identity import fingerprint
from .quality_validation import FrozenQualityManifestV2, ThresholdProfileV1
from .reporting import write_json
from .telemetry import TelemetryEnvelopeV1

Arm = Literal["baseline", "candidate"]


class _Versioned(Contract):
    schema_version: Literal[1] = 1

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int:
            raise ValueError("quality connection versions must be integers")
        return value


def _instant(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ValueError("quality connection timestamps require UTC")
    return result


class QualityFormalCaseV1(Contract):
    case_id: Digest
    sample_id: Text
    repetition: Annotated[int, Field(ge=1, le=2)]
    quality_job_index: Annotated[int, Field(ge=0, le=9999)]


class QualityArmPlanV1(Contract):
    arm: Arm
    bundle_path: str
    manifest_file_sha256: Digest
    bundle_seal_sha256: Digest
    experiment_identity: ExperimentIdentityV1
    validation_path: Literal["primary_only", "primary_plus_local_ocr"]
    secondary_tool_sha256: Digest | None
    formal_cases: Annotated[tuple[QualityFormalCaseV1, ...], Field(min_length=1, max_length=10000)]

    @field_validator("bundle_path")
    @classmethod
    def portable(cls, value):
        _relative(value)
        return value

    @model_validator(mode="after")
    def unique_cases(self) -> Self:
        if len({x.case_id for x in self.formal_cases}) != len(self.formal_cases) or sorted(
            x.quality_job_index for x in self.formal_cases
        ) != list(range(len(self.formal_cases))):
            raise ValueError("quality plan must cover each formal case and quality job exactly once")
        if (self.validation_path == "primary_plus_local_ocr") != (self.secondary_tool_sha256 is not None):
            raise ValueError("quality plan tool binding must match its selected path")
        return self


class BenchmarkQualityPlanV1(_Versioned):
    kind: Literal["a4_benchmark_quality_plan"] = "a4_benchmark_quality_plan"
    spec_sha256: Digest
    dataset_sha256: Digest
    labels_sha256: Digest
    threshold_profile_sha256: Digest
    agreement_file_sha256: Digest | None
    frozen_at: Text
    baseline: QualityArmPlanV1
    candidate: QualityArmPlanV1
    plan_sha256: Digest

    @model_validator(mode="after")
    def sealed_plan(self) -> Self:
        _instant(self.frozen_at)
        if self.baseline.arm != "baseline" or self.candidate.arm != "candidate":
            raise ValueError("quality plan arms cannot be exchanged")
        if self.plan_sha256 != fingerprint(self.model_dump(mode="json", exclude={"plan_sha256"})):
            raise ValueError("quality plan differs from its content hash")
        return self


class QualityMeasuredCaseV1(Contract):
    case_id: Digest
    measurement_id: Text
    telemetry_sha256: Digest
    report_sha256: Digest


class QualityArmLinkV1(Contract):
    arm: Arm
    run_path: str
    receipt_path: str
    execution_binding_path: str
    receipt_file_sha256: Digest
    receipt_sha256: Digest
    execution_file_sha256: Digest
    formal_cases: Annotated[tuple[QualityMeasuredCaseV1, ...], Field(min_length=1, max_length=10000)]

    @field_validator("run_path", "receipt_path", "execution_binding_path")
    @classmethod
    def portable(cls, value):
        _relative(value)
        return value

    @model_validator(mode="after")
    def unique_cases(self) -> Self:
        if len({x.case_id for x in self.formal_cases}) != len(self.formal_cases) or len({
            x.measurement_id for x in self.formal_cases
        }) != len(self.formal_cases):
            raise ValueError("quality links require unique case and measurement identities")
        return self


class BenchmarkQualityLinkV1(_Versioned):
    kind: Literal["a4_benchmark_quality_link"] = "a4_benchmark_quality_link"
    spec_sha256: Digest
    plan_path: str
    plan_file_sha256: Digest
    plan_sha256: Digest
    baseline: QualityArmLinkV1
    candidate: QualityArmLinkV1
    link_sha256: Digest

    @field_validator("plan_path")
    @classmethod
    def portable(cls, value):
        _relative(value)
        return value

    @model_validator(mode="after")
    def sealed_link(self) -> Self:
        if self.baseline.arm != "baseline" or self.candidate.arm != "candidate":
            raise ValueError("quality connection arms cannot be exchanged")
        if self.link_sha256 != fingerprint(self.model_dump(mode="json", exclude={"link_sha256"})):
            raise ValueError("quality connection differs from its content hash")
        return self


class QualityEvidenceReferenceV1(Contract):
    role: Literal["link", "plan", "manifest", "receipt", "execution"]
    arm: Arm | None
    relative_path: str
    expected_sha256: Digest | None
    actual_sha256: Digest | None
    status: Literal["matched", "missing", "different", "read"]

    @field_validator("relative_path")
    @classmethod
    def portable(cls, value):
        _relative(value)
        return value


class BenchmarkQualityAssessmentV1(_Versioned):
    kind: Literal["a4_benchmark_quality_assessment"] = "a4_benchmark_quality_assessment"
    status: Literal["verified", "unavailable", "rejected"]
    quality_acceptance: Literal["not_assessed", "passed", "failed"]
    reason_codes: tuple[Text, ...]
    references: tuple[QualityEvidenceReferenceV1, ...]
    arms: dict
    timestamp_claim: Literal["protocol_binding_only"] = "protocol_binding_only"

    @model_validator(mode="after")
    def assessable(self) -> Self:
        if self.quality_acceptance != "not_assessed" and (self.status != "verified" or self.reason_codes):
            raise ValueError("quality acceptance requires complete verified connection evidence")
        return self


def _seal(model, field, values):
    packet = json.loads(json.dumps(values, default=lambda x: x.model_dump(mode="json"), allow_nan=False))
    packet.setdefault("schema_version", 1)
    packet.setdefault("kind", "a4_benchmark_quality_plan" if model is BenchmarkQualityPlanV1 else "a4_benchmark_quality_link")
    packet.pop(field, None)
    return model.model_validate_json(json.dumps({**packet, field: fingerprint(packet)}))


def make_quality_link(**fields) -> BenchmarkQualityLinkV1:
    """Seal explicit postmeasurement references; never derive evidence or run audits."""
    try:
        return _seal(BenchmarkQualityLinkV1, "link_sha256", fields)
    except (ValueError, TypeError, AttributeError):
        raise InputError("invalid explicit benchmark quality connection") from None


def _within(root: Path, relative: str) -> Path:
    _relative(relative)
    result = root / relative
    if any(item.is_symlink() for item in (result, *result.parents)):
        raise InputError("quality connection must not follow symlinks")
    return result


def _logical_dataset(dataset: Dataset):
    return {sample.sample_id: sample.model_dump(mode="json", exclude={"input_path", "response_path"})
            for sample in dataset.samples}


def _arm_plan(spec, arm, root, bundle):
    relative = bundle.relative_to(root).as_posix()
    _within(root, relative)
    raw = _read(bundle / "manifest.json")
    manifest = FrozenQualityManifestV2.model_validate_json(json.dumps(_json(raw)))
    dataset = Dataset.model_validate_json(json.dumps(_json(_read(bundle / "dataset.json"))))
    selected = [sample for sample in dataset.samples if sample.split == "validation"]
    if tuple(sample.sample_id for sample in selected) != spec.sample_ids or len(selected) != len(dataset.samples):
        raise InputError("quality plan must cover exactly the benchmark validation samples")
    if (manifest.plan.repeats != spec.repetitions
            or manifest.threshold_state != ("agreed" if spec.quality_gate.state == "agreed" else "draft")
            or (spec.quality_gate.state == "agreed" and manifest.threshold_profile_sha256 != spec.quality_gate.threshold_profile_sha256)):
        raise InputError("quality frozen repetition or threshold plan differs from benchmark")
    identity_sha = getattr(spec, arm + "_identity_sha256")
    if manifest.experiment_identity.identity_sha256 != identity_sha:
        raise InputError("quality frozen identity differs from its benchmark arm")
    binding = {x.sample_id: (x.input_fingerprint, x.image_sha256) for x in spec.sample_bindings}
    if {x.sample_id: (x.input_sha256, x.image_sha256) for x in manifest.samples} != binding:
        raise InputError("quality frozen sample binding differs from benchmark")
    jobs = {(sample.sample_id, repetition): (repetition - 1) * len(selected) + index
            for repetition in range(1, spec.repetitions + 1) for index, sample in enumerate(selected)}
    formal = tuple(QualityFormalCaseV1(case_id=case.case_id, sample_id=case.sample_id, repetition=case.repetition,
        quality_job_index=jobs[case.sample_id, case.repetition]) for case in planned_cases(spec) if case.arm == arm and not case.warmup)
    return QualityArmPlanV1(arm=arm, bundle_path=relative, manifest_file_sha256=hashlib.sha256(raw).hexdigest(),
        bundle_seal_sha256=manifest.seal_sha256, experiment_identity=manifest.experiment_identity,
        validation_path=manifest.validation_path, secondary_tool_sha256=manifest.secondary_tool_sha256, formal_cases=formal), dataset


def freeze_benchmark_quality_plan(spec: BenchmarkSpecV1, out: str | Path, *,
                                  baseline_bundle: str | Path, candidate_bundle: str | Path) -> BenchmarkQualityPlanV1:
    """Record actual UTC and known two-arm policies before any measurement.

    Paths are portable beneath the plan's directory. The immutable output is
    only a commitment; subsequent consumption independently verifies archives.
    No caller-supplied timestamp or already-produced telemetry is accepted.
    """
    spec = _checked_spec(spec)
    destination = Path(out).expanduser().absolute()
    if destination.exists() or any(x.is_symlink() for x in (destination, *destination.parents)):
        raise InputError("quality plan output must be a new nonsymlink file")
    if spec.labels_sha256 is None:
        raise InputError("quality connection plan requires fixed explicit labels")
    root = destination.parent
    try:
        baseline, first_dataset = _arm_plan(spec, "baseline", root, Path(baseline_bundle).expanduser().absolute())
        candidate, second_dataset = _arm_plan(spec, "candidate", root, Path(candidate_bundle).expanduser().absolute())
        if first_dataset.purpose != second_dataset.purpose or _logical_dataset(first_dataset) != _logical_dataset(second_dataset):
            raise InputError("quality arm logical datasets differ")
        agreements, threshold_hashes = [], []
        frozen_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for arm_plan in (baseline, candidate):
            bundle = _within(root, arm_plan.bundle_path)
            labels = LabelSheet.model_validate_json(json.dumps(_json(_read(bundle / "labels.json"))))
            thresholds = ThresholdProfileV1.model_validate_json(json.dumps(_json(_read(bundle / "thresholds.json"))))
            manifest = FrozenQualityManifestV2.model_validate_json(json.dumps(_json(_read(bundle / "manifest.json"))))
            actual_threshold_sha256 = fingerprint(thresholds.model_dump(mode="json"))
            if (fingerprint(labels.model_dump(mode="json")) != spec.labels_sha256
                    or actual_threshold_sha256 != manifest.threshold_profile_sha256
                    or thresholds.state != manifest.threshold_state
                    or thresholds.state != ("agreed" if spec.quality_gate.state == "agreed" else "draft")
                    or hashlib.sha256(_read(bundle / "manifest.json")).hexdigest() != arm_plan.manifest_file_sha256):
                raise InputError("quality plan labels, thresholds or agreement differ")
            threshold_hashes.append(actual_threshold_sha256)
            if thresholds.state == "agreed":
                agreement = _read(bundle / "agreement.bin")
                if (actual_threshold_sha256 != spec.quality_gate.threshold_profile_sha256
                        or _instant(thresholds.agreed_at) > _instant(frozen_at)
                        or not agreement.strip() or hashlib.sha256(agreement).hexdigest() != thresholds.agreement_evidence_sha256):
                    raise InputError("quality plan agreed threshold evidence differs")
                agreements.append(hashlib.sha256(agreement).hexdigest())
            else:
                agreement_path = bundle / "agreement.bin"
                if agreement_path.exists() or agreement_path.is_symlink():
                    raise InputError("draft quality plan cannot imply agreement evidence")
                agreements.append(None)
        if len(set(agreements)) != 1 or len(set(threshold_hashes)) != 1:
            raise InputError("quality arms must use the same fixed threshold and agreement evidence")
        plan = _seal(BenchmarkQualityPlanV1, "plan_sha256", dict(spec_sha256=spec.spec_sha256,
            dataset_sha256=spec.dataset_sha256, labels_sha256=spec.labels_sha256,
            threshold_profile_sha256=threshold_hashes[0],
            agreement_file_sha256=agreements[0], frozen_at=frozen_at, baseline=baseline, candidate=candidate))
        write_json(destination, plan.model_dump(mode="json"))
        return plan
    except (ValueError, TypeError, AttributeError, OSError):
        raise InputError("cannot freeze matching benchmark quality policies") from None


class _Rejected(Exception):
    def __init__(self, code, *, missing=False):
        self.code, self.missing = code, missing


def _reference(root, relative, expected, role, arm, references):
    path = _within(root, relative)
    try:
        raw = _read(path)
    except InputError:
        references.append(QualityEvidenceReferenceV1(role=role, arm=arm, relative_path=relative,
            expected_sha256=expected, actual_sha256=None, status="missing"))
        raise _Rejected("quality_" + role + "_unavailable", missing=True) from None
    actual = hashlib.sha256(raw).hexdigest()
    references.append(QualityEvidenceReferenceV1(role=role, arm=arm, relative_path=relative,
        expected_sha256=expected, actual_sha256=actual, status="matched" if expected == actual else "read" if expected is None else "different"))
    if expected is not None and actual != expected:
        raise _Rejected("quality_" + role + "_hash_mismatch")
    return path, raw


def _checked_connection(spec, plan, link):
    if (link.spec_sha256 != spec.spec_sha256 or plan.spec_sha256 != spec.spec_sha256
            or link.plan_sha256 != plan.plan_sha256 or plan.dataset_sha256 != spec.dataset_sha256
            or plan.labels_sha256 != spec.labels_sha256
            or (spec.quality_gate.state == "agreed" and plan.threshold_profile_sha256 != spec.quality_gate.threshold_profile_sha256)
            or (spec.quality_gate.state == "agreed") != (plan.agreement_file_sha256 is not None)):
        raise _Rejected("quality_plan_spec_mismatch")
    for arm in ("baseline", "candidate"):
        selected = getattr(plan, arm)
        formal = [case for case in planned_cases(spec) if case.arm == arm and not case.warmup]
        if (selected.experiment_identity.identity_sha256 != getattr(spec, arm + "_identity_sha256")
                or {x.case_id for x in selected.formal_cases} != {x.case_id for x in formal}
                or {x.case_id for x in getattr(link, arm).formal_cases} != {x.case_id for x in formal}):
            raise _Rejected("quality_arm_formal_plan_mismatch")
        expected = {case.case_id: case for case in formal}
        if any((item.sample_id, item.repetition) != (expected[item.case_id].sample_id, expected[item.case_id].repetition)
               for item in selected.formal_cases):
            raise _Rejected("quality_case_semantics_mismatch")


def _check_archive(spec, plan, arm_plan, archive, dataset, labels):
    manifest, receipt = archive.manifest, archive.receipt
    if (not isinstance(manifest, FrozenQualityManifestV2) or receipt.schema_version != 2
            or manifest.seal_sha256 != arm_plan.bundle_seal_sha256
            or manifest.experiment_identity != arm_plan.experiment_identity
            or manifest.validation_path != arm_plan.validation_path
            or manifest.secondary_tool_sha256 != arm_plan.secondary_tool_sha256
            or receipt.experiment_identity_sha256 != arm_plan.experiment_identity.identity_sha256
            or receipt.bundle_seal_sha256 != manifest.seal_sha256):
        raise _Rejected("quality_archive_arm_binding_mismatch")
    if (dataset.purpose != archive.dataset.purpose or _logical_dataset(dataset) != _logical_dataset(archive.dataset)
            or fingerprint(archive.labels.model_dump(mode="json")) != plan.labels_sha256
            or labels is None or archive.labels != labels
            or fingerprint(archive.thresholds.model_dump(mode="json")) != plan.threshold_profile_sha256
            or archive.thresholds.state != ("agreed" if spec.quality_gate.state == "agreed" else "draft")
            or manifest.threshold_state != archive.thresholds.state
            or archive.agreement_file_sha256 != plan.agreement_file_sha256
            or (spec.quality_gate.state == "agreed" and _instant(archive.thresholds.agreed_at) > _instant(plan.frozen_at))):
        raise _Rejected("quality_archive_dataset_or_agreement_mismatch")
    if manifest.plan.repeats != spec.repetitions or len(archive.jobs) != len(arm_plan.formal_cases):
        raise _Rejected("quality_archive_denominator_mismatch")
    bindings = {x.sample_id: (x.input_fingerprint, x.image_sha256) for x in spec.sample_bindings}
    if {x.sample_id: (x.input_sha256, x.image_sha256) for x in manifest.samples} != bindings:
        raise _Rejected("quality_archive_sample_binding_mismatch")
    for item in arm_plan.formal_cases:
        job = archive.jobs[item.quality_job_index]
        if ((job["sample_id"], job["repeat"]) != (item.sample_id, item.repetition)
                or (job["input_sha256"], job["image_sha256"]) != bindings[item.sample_id]):
            raise _Rejected("quality_archive_job_binding_mismatch")


def _check_measurements(plan, arm_plan, arm_link, archive, loaded_records):
    measured = {x.case_id: x for x in arm_link.formal_cases}
    by_case = {}
    for record in loaded_records:
        value = record["value"]
        if isinstance(value, TelemetryEnvelopeV1) and value.benchmark_case is not None and value.benchmark_case.arm == arm_plan.arm:
            by_case.setdefault(value.benchmark_case.case_id, []).append(record)
    for item in arm_plan.formal_cases:
        choices = by_case.get(item.case_id, ())
        if len(choices) != 1 or choices[0]["status"] != "recorded" or choices[0]["error"] is not None:
            raise _Rejected("quality_formal_measurement_unavailable", missing=True)
        record, expected = choices[0], measured[item.case_id]
        value = TelemetryEnvelopeV1.model_validate_json(json.dumps(_json(record["raw"])))
        if (hashlib.sha256(record["raw"]).hexdigest() != expected.telemetry_sha256
                or value.measurement_id != expected.measurement_id
                or value != record["value"] or record["arm"] != arm_plan.arm
                or value.benchmark_case is None or value.benchmark_case.warmup
                or value.benchmark_case.case_id != item.case_id or value.scope != "audit_only"
                or value.delivery_state != "not_called" or value.measurement_status != "recorded"
                or any(stage.name == "delivery" and stage.outcome != "not_run" for stage in value.stages)
                or value.experiment_identity != arm_plan.experiment_identity.model_dump(mode="json")
                or _instant(value.started_at) < _instant(plan.frozen_at)):
            raise _Rejected("quality_measurement_identity_or_time_mismatch")
        index = item.quality_job_index
        report = archive.reports.get(index)
        row = archive.rows.get(index)
        if row is None or archive.artifacts.get(f"{index:04d}.json") != expected.report_sha256:
            raise _Rejected("quality_full_report_unavailable", missing=True)
        if (archive.artifacts.get(f"{index:04d}.telemetry.json") != expected.telemetry_sha256
                or row["elapsed_ms"] != round(value.elapsed_total_ms, 3)):
            raise _Rejected("quality_same_invocation_telemetry_missing_or_mismatched")
        if value.calls != archive.case_calls.get(index) or value.audit_decision != row["decision"]:
            raise _Rejected("quality_measurement_report_call_mismatch")
        if report is not None:
            if (value.terminal_outcome != "completed" or report.decision != value.audit_decision
                    or report.sample_id != item.sample_id or report.prompt_version != arm_plan.experiment_identity.prompt_version
                    or report.input_fingerprint != value.input_fingerprint or report.image_sha256 != value.image_sha256
                    or report.calls != value.calls or report.execution_mode != value.execution_mode):
                raise _Rejected("quality_measurement_full_report_mismatch")
        elif value.terminal_outcome not in ("input_error", "execution_error", "cancelled") or value.error_type != row["error_type"]:
            raise _Rejected("quality_measurement_error_slot_mismatch")


def _aggregate_quality(spec, benchmark_status, all_formal_live, arm_results):
    """Pure final aggregation, called only after both complete archive joins."""
    reasons = []
    if set(arm_results) != {"baseline", "candidate"}:
        return "not_assessed", ("quality_two_arm_evidence_missing",)
    if spec.measurement_kind != "live_service" or not all_formal_live or any(
        result["evidence_kind"] != "live_service" for result in arm_results.values()
    ):
        reasons.append("quality_actual_live_service_evidence_missing")
    if benchmark_status != "complete":
        reasons.append("quality_benchmark_plan_incomplete_or_rejected")
    if spec.quality_gate.state != "agreed" or spec.labels_sha256 is None:
        reasons.append("quality_labels_or_thresholds_missing")
    if any(result["source_purpose"] != "human_labeled" or result["label_kind"] != "independent_human"
           for result in arm_results.values()):
        reasons.append("quality_independent_human_labels_missing")
    if any(result["status"] != "complete" or result["quality_acceptance"] not in ("passed", "failed") or result["reason_codes"]
           for result in arm_results.values()):
        reasons.append("quality_archive_acceptance_not_assessed")
    acceptance = "not_assessed" if reasons else "failed" if any(
        result["quality_acceptance"] == "failed" for result in arm_results.values()
    ) else "passed"
    return acceptance, tuple(reasons)


def verify_benchmark_quality(spec: BenchmarkSpecV1, *, link_path: str | Path,
                              loaded_records: list[dict], dataset: Dataset, labels: LabelSheet | None,
                              benchmark_status: str) -> BenchmarkQualityAssessmentV1:
    """Read and join full quality archives, retaining controlled rejection evidence."""
    references, arm_results = [], {}
    try:
        spec = _checked_spec(spec)
        supplied = Path(link_path).expanduser().absolute()
        root = supplied.parent
        if any(item.is_symlink() for item in (supplied, *supplied.parents)):
            raise _Rejected("quality_link_path_invalid")
        # The filename is deliberately not exported. Only controlled links
        # inside its document appear in reusable output references.
        if not supplied.exists():
            references.append(QualityEvidenceReferenceV1(role="link", arm=None, relative_path="quality_link.json",
                expected_sha256=None, actual_sha256=None, status="missing"))
            raise _Rejected("quality_link_unavailable", missing=True)
        raw = _read(supplied)
        references.append(QualityEvidenceReferenceV1(role="link", arm=None, relative_path="quality_link.json",
            expected_sha256=None, actual_sha256=hashlib.sha256(raw).hexdigest(), status="read"))
        link = BenchmarkQualityLinkV1.model_validate_json(json.dumps(_json(raw)))
        _, plan_raw = _reference(root, link.plan_path, link.plan_file_sha256, "plan", None, references)
        plan = BenchmarkQualityPlanV1.model_validate_json(json.dumps(_json(plan_raw)))
        _checked_connection(spec, plan, link)
        try:
            from .quality_archive import verify_archived_quality
        except ImportError:
            raise _Rejected("quality_archive_verifier_unavailable", missing=True) from None
        all_formal_live = True
        for arm in ("baseline", "candidate"):
            arm_plan, arm_link = getattr(plan, arm), getattr(link, arm)
            bundle_relative = (Path(link.plan_path).parent / arm_plan.bundle_path).as_posix()
            bundle = _within(root, bundle_relative)
            _, manifest_raw = _reference(root, bundle_relative + "/manifest.json", arm_plan.manifest_file_sha256, "manifest", arm, references)
            receipt_path, _ = _reference(root, arm_link.receipt_path, arm_link.receipt_file_sha256, "receipt", arm, references)
            execution_path, _ = _reference(root, arm_link.execution_binding_path, arm_link.execution_file_sha256, "execution", arm, references)
            archive = verify_archived_quality(bundle, _within(root, arm_link.run_path), receipt_path, execution_path)
            if (archive.manifest_file_sha256 != hashlib.sha256(manifest_raw).hexdigest()
                    or archive.receipt_file_sha256 != arm_link.receipt_file_sha256
                    or archive.execution_file_sha256 != arm_link.execution_file_sha256
                    or archive.receipt.receipt_sha256 != arm_link.receipt_sha256):
                raise _Rejected("quality_archive_file_binding_mismatch")
            _check_archive(spec, plan, arm_plan, archive, dataset, labels)
            _check_measurements(plan, arm_plan, arm_link, archive, loaded_records)
            values = [r["value"] for r in loaded_records if r["status"] == "recorded" and isinstance(r["value"], TelemetryEnvelopeV1)
                      and r["value"].benchmark_case.arm == arm and not r["value"].benchmark_case.warmup]
            all_formal_live &= all(v.execution_mode == "live" and v.measurement_kind == "live_service" for v in values)
            receipt = archive.receipt
            arm_results[arm] = {"experiment_identity_sha256": arm_plan.experiment_identity.identity_sha256,
                "bundle_seal_sha256": arm_plan.bundle_seal_sha256, "receipt_sha256": receipt.receipt_sha256,
                "evidence_kind": receipt.evidence_kind, "status": receipt.status,
                "source_purpose": archive.manifest.source_purpose, "label_kind": archive.manifest.label_kind,
                "quality_acceptance": receipt.quality_acceptance, "reason_codes": list(receipt.reason_codes),
                "verified_formal_cases": len(values), "metrics": receipt.metrics, "quality_gates": list(receipt.quality_gates)}
        acceptance, reasons = _aggregate_quality(spec, benchmark_status, all_formal_live, arm_results)
        return BenchmarkQualityAssessmentV1(status="verified", quality_acceptance=acceptance,
            reason_codes=reasons, references=tuple(references), arms=arm_results)
    except _Rejected as exc:
        return BenchmarkQualityAssessmentV1(status="unavailable" if exc.missing else "rejected", quality_acceptance="not_assessed",
            reason_codes=(exc.code,), references=tuple(references), arms=arm_results)
    except (InputError, ValueError, TypeError, KeyError, IndexError, AttributeError, OSError, UnicodeError):
        return BenchmarkQualityAssessmentV1(status="rejected", quality_acceptance="not_assessed",
            reason_codes=("quality_archive_or_connection_invalid",), references=tuple(references), arms=arm_results)

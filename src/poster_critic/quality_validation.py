"""Frozen, audit-only quality validation with explicit independent-label gates.

Nothing in this module generates labels, agrees thresholds, dispatches a poster,
or treats an HTTP 200 as accuracy. A receipt verifies local evidence and declared
provenance, not the human identity of an annotator or a vendor's billing invoice.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
from importlib import metadata
from importlib.resources import files
import json
from pathlib import Path
import platform
from types import SimpleNamespace
from typing import Annotated, Literal
from collections.abc import Mapping

from pydantic import Field, StringConstraints, field_validator, model_validator

from .contracts import AuditReportV3, Contract, Text, parse_report
from .data import LabelSheet
from .engine import PROMPT_VERSION
from .errors import InputError, ProviderError
from .evaluate import Dataset, run_dataset
from .evaluation_io import read_results, read_run
from .frozen_evaluation import (_decode_json, _json_bytes, _parse, _prepare_dataset,
                                _read, _reference, _relative, _safe_path)
from .identity import fingerprint, software_fingerprint
from .providers.chat import ModelCatalog, ProviderConfig
from .reporting import file_lock, usage_summary, write_bytes, write_json
from .experiment_identity import ExperimentIdentityV1, build_experiment_identity, supplemental_policy_fingerprint
from .provider_capabilities import ProviderCapabilityV1, load_capability, prepare_request
from .language_profiles import LanguageProfileV1, legacy_language_profile, read_language_profile, validate_language_profile
from .evidence_review import SecondaryReviewConfigV1, SupplementalReviewV1, EvidenceReviewer
from .macos_vision import VisionHelperV1, VisionProducerV1, VisionAttemptV1, VisionOCR, read_vision_helper
from .ocr import OCRAttemptV1, TesseractOCR
from .quality_tesseract import FrozenTesseractToolV1, freeze_tesseract_tool, validate_frozen_tesseract_tool

SHA256 = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]
Metric = Literal['decision_agreement', 'problem_detection', 'problem_false_pass',
                 'normal_false_fail', 'unknown_or_execution_failure',
                 'first_valid_response', 'repeat_decision_consistency']


class VersionedContract(Contract):
    schema_version: Literal[1] = 1

    @field_validator('schema_version', mode='before')
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError('schema version must be an integer')
        return value


class QualityGateV1(VersionedContract):
    metric: Metric
    scope: Literal['all', 'each_repeat'] = 'all'
    minimum_rate: float | None = Field(default=None, ge=0, le=1)
    maximum_rate: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode='after')
    def meaningful(self):
        if self.minimum_rate is None and self.maximum_rate is None:
            raise ValueError('a gate needs an explicit minimum or maximum')
        if (self.minimum_rate is not None and self.maximum_rate is not None
                and self.minimum_rate > self.maximum_rate):
            raise ValueError('gate minimum exceeds maximum')
        if self.metric == 'repeat_decision_consistency' and self.scope != 'all':
            raise ValueError('repeat consistency is evaluated across repeats')
        return self


class ThresholdProfileV1(VersionedContract):
    kind: Literal['a4_quality_thresholds'] = 'a4_quality_thresholds'
    profile_id: Text
    state: Literal['draft', 'agreed']
    validation_samples: int = Field(ge=1)
    repeats: Literal[1, 2]
    gates: tuple[QualityGateV1, ...] = Field(min_length=1)
    agreement_reference: Text | None = None
    agreed_by: Text | None = None
    agreed_at: Text | None = None
    agreement_evidence_sha256: SHA256 | None = None

    @field_validator('repeats', mode='before')
    @classmethod
    def integer_repeats(cls, value):
        if type(value) is not int:
            raise ValueError('repeats must be an integer')
        return value

    @model_validator(mode='after')
    def explicit_agreement(self):
        agreement = (self.agreement_reference, self.agreed_by,
                     self.agreed_at, self.agreement_evidence_sha256)
        if self.state == 'agreed' and any(item is None for item in agreement):
            raise ValueError('agreed thresholds require frozen agreement evidence')
        if self.state == 'draft' and any(item is not None for item in agreement):
            raise ValueError('draft thresholds cannot imply agreement')
        if self.agreed_at is not None:
            from datetime import datetime
            try:
                value = datetime.fromisoformat(self.agreed_at.replace('Z', '+00:00'))
            except ValueError:
                raise ValueError('agreement time must be an ISO timestamp with timezone') from None
            if value.tzinfo is None:
                raise ValueError('agreement time requires a timezone')
        ids = [(gate.metric, gate.scope) for gate in self.gates]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate quality gate')
        required = {'problem_detection', 'problem_false_pass', 'normal_false_fail',
                    'unknown_or_execution_failure', 'first_valid_response'}
        if self.repeats == 2:
            required.add('repeat_decision_consistency')
        elif any(g.metric == 'repeat_decision_consistency' for g in self.gates):
            raise ValueError('repeat consistency requires two repeats')
        if not required.issubset({g.metric for g in self.gates}):
            raise ValueError('quality thresholds must cover detection, false pass/fail, unknown and validity')
        return self


class QualityArtifactV1(VersionedContract):
    path: Text
    sha256: SHA256
    size_bytes: int = Field(ge=0, le=20 * 1024 * 1024)
    kind: Literal['dataset', 'input', 'image', 'observation', 'labels', 'config',
                  'thresholds', 'agreement', 'software']

    @field_validator('path')
    @classmethod
    def contained_path(cls, value):
        return _relative(value)


class QualitySampleBindingV1(VersionedContract):
    sample_id: Text
    input_sha256: SHA256
    image_sha256: SHA256


class QualityPlanV1(VersionedContract):
    split: Literal['validation'] = 'validation'
    repeats: Literal[1, 2]
    max_attempts_per_audit: Literal[1, 2] = 2
    max_total_attempts: int = Field(ge=1)
    request_timeout: float = Field(gt=0, le=300)
    total_timeout: float = Field(gt=0, le=600)

    @field_validator('repeats', 'max_attempts_per_audit', mode='before')
    @classmethod
    def integer_counts(cls, value):
        if type(value) is not int:
            raise ValueError('repeat and attempt counts must be integers')
        return value


class FrozenQualityManifestV1(VersionedContract):
    kind: Literal['a4_frozen_quality_validation'] = 'a4_frozen_quality_validation'
    source_purpose: Literal['software_demo', 'human_labeled']
    label_kind: Literal['independent_human', 'model_proxy', 'software_control', 'unverified']
    label_reference: Text
    annotator: Text
    threshold_state: Literal['draft', 'agreed']
    threshold_profile_sha256: SHA256
    prompt_version: Text
    software_fingerprint_v1: SHA256
    source_inventory_sha256: SHA256
    environment: dict[str, str | None]
    provider_config_sha256: SHA256 | None
    plan: QualityPlanV1
    samples: tuple[QualitySampleBindingV1, ...] = Field(min_length=1)
    artifacts: tuple[QualityArtifactV1, ...] = Field(min_length=1)
    seal_sha256: SHA256

    @model_validator(mode='after')
    def consistent(self):
        names = [item.path for item in self.artifacts]
        if len(names) != len(set(names)) or any(n in names for n in ('manifest.json', '.incomplete.json')):
            raise ValueError('duplicate or reserved artifact name')
        if len({s.sample_id for s in self.samples}) != len(self.samples):
            raise ValueError('duplicate sample binding')
        if fingerprint(self.model_dump(mode='json', exclude={'seal_sha256'})) != self.seal_sha256:
            raise ValueError('quality manifest seal differs')
        return self


class QualityReceiptV1(VersionedContract):
    kind: Literal['a4_quality_receipt'] = 'a4_quality_receipt'
    bundle_seal_sha256: SHA256
    run_fingerprint: SHA256
    evidence_kind: Literal['live_service', 'fake_replay', 'mock_transport', 'unverified']
    status: Literal['complete', 'incomplete']
    quality_acceptance: Literal['not_assessed', 'passed', 'failed']
    reason_codes: tuple[Text, ...]
    planned_audits: int = Field(ge=1)
    recorded_audits: int = Field(ge=0)
    completed_result_files: int = Field(ge=0)
    missing_audits: int = Field(ge=0)
    execution_errors: int = Field(ge=0)
    unknown_decisions: int = Field(ge=0)
    metrics: dict
    per_repeat: dict
    quality_gates: tuple[dict, ...]
    usage: dict
    artifacts: tuple[QualityArtifactV1, ...]
    receipt_sha256: SHA256

    @model_validator(mode='after')
    def valid_receipt(self):
        if self.recorded_audits + self.missing_audits != self.planned_audits:
            raise ValueError('receipt denominator does not match the frozen plan')
        if self.completed_result_files > self.recorded_audits:
            raise ValueError('more result files than recorded audits')
        if self.quality_acceptance != 'not_assessed' and (self.status != 'complete'
                or self.evidence_kind != 'live_service' or self.reason_codes):
            raise ValueError('quality conclusion requires complete verified live evidence')
        if fingerprint(self.model_dump(mode='json', exclude={'receipt_sha256'})) != self.receipt_sha256:
            raise ValueError('quality receipt seal differs')
        return self


class QualityStartV1(VersionedContract):
    kind: Literal['a4_quality_start'] = 'a4_quality_start'
    bundle_seal_sha256: SHA256
    evidence_kind: Literal['live_service', 'fake_replay', 'mock_transport']
    plan: QualityPlanV1
    call_schema_version: Literal[2] = 2


class QualityExecutionV1(VersionedContract):
    kind: Literal['a4_quality_execution'] = 'a4_quality_execution'
    bundle_seal_sha256: SHA256
    run_fingerprint: SHA256
    evidence_kind: Literal['live_service', 'fake_replay', 'mock_transport']
    call_schema_version: Literal[2] = 2
    start_sha256: SHA256
    run_artifacts_sha256: SHA256
    actual_calls_sha256: SHA256
    execution_sha256: SHA256

    @model_validator(mode='after')
    def seal(self):
        if fingerprint(self.model_dump(mode='json', exclude={'execution_sha256'})) != self.execution_sha256:
            raise ValueError('quality execution seal differs')
        return self




class FrozenQualityArtifactV2(QualityArtifactV1):
    schema_version: Literal[2] = 2
    kind: Literal['dataset', 'input', 'image', 'observation', 'labels', 'config',
                  'thresholds', 'agreement', 'software', 'capability', 'language', 'tool_identity']


class FrozenQualityManifestV2(FrozenQualityManifestV1):
    schema_version: Literal[2] = 2
    validation_path: Literal['primary_only', 'primary_plus_local_ocr']
    execution_mode: Literal['fake', 'live']
    experiment_identity: ExperimentIdentityV1
    capability_sha256: SHA256 | None
    language_profile_sha256: SHA256
    secondary_policy_sha256: SHA256 | None
    secondary_tool_sha256: SHA256 | None
    endpoint_family: Text
    region: Text | None
    artifacts: tuple[FrozenQualityArtifactV2, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def policy_bindings(self):
        identity = self.experiment_identity
        if (identity.capability_sha256 != self.capability_sha256
                or identity.language_profile_sha256 != self.language_profile_sha256
                or identity.supplemental_policy_sha256 != self.secondary_policy_sha256
                or identity.provider_config_sha256 != self.provider_config_sha256):
            raise ValueError('quality policies differ from the experiment identity')
        _manifest_client_policy(self)
        selected = self.validation_path == 'primary_plus_local_ocr'
        if selected != (self.secondary_policy_sha256 is not None and self.secondary_tool_sha256 is not None):
            raise ValueError('quality path must match its frozen secondary policy and tool')
        if not selected and (self.secondary_policy_sha256 is not None or self.secondary_tool_sha256 is not None):
            raise ValueError('primary-only quality cannot claim secondary evidence')
        return self


class FrozenVisionToolV1(VersionedContract):
    kind: Literal['a4_frozen_vision_tool'] = 'a4_frozen_vision_tool'
    helper: VisionHelperV1
    producer: VisionProducerV1
    config_sha256: SHA256
    secondary_policy_sha256: SHA256
    safe_policy: dict
    tool_sha256: SHA256

    @model_validator(mode='after')
    def tool_binding(self):
        if fingerprint(self.model_dump(mode='json', exclude={'tool_sha256'})) != self.tool_sha256:
            raise ValueError('frozen local tool binding differs')
        if 'helper_directory' in json.dumps(self.safe_policy):
            raise ValueError('quality tool object cannot retain private helper paths')
        return self


class QualityReceiptV2(QualityReceiptV1):
    schema_version: Literal[2] = 2
    validation_path: Literal['primary_only', 'primary_plus_local_ocr']
    execution_mode: Literal['fake', 'live']
    experiment_identity_sha256: SHA256
    capability_sha256: SHA256 | None
    language_profile_sha256: SHA256
    secondary_policy_sha256: SHA256 | None
    secondary_tool_sha256: SHA256 | None
    local_tool_attempts: int | None = Field(ge=0)
    local_tool_process_starts: int | None = Field(ge=0)
    local_tool_known_attempts: int = Field(ge=0)
    local_tool_known_process_starts: int = Field(ge=0)
    local_tool_unresolved_invocations: int = Field(ge=0)
    claim_scope: Literal['selected_audit_path_only'] = 'selected_audit_path_only'

    @model_validator(mode='after')
    def tool_accounting(self):
        if self.local_tool_unresolved_invocations:
            if (self.local_tool_attempts is not None or self.local_tool_process_starts is not None
                    or self.quality_acceptance != 'not_assessed'
                    or 'local_tool_accounting_incomplete' not in self.reason_codes):
                raise ValueError('unfinished tool invocations require unknown totals and no quality claim')
        elif (self.local_tool_attempts != self.local_tool_known_attempts
                or self.local_tool_process_starts != self.local_tool_known_process_starts):
            raise ValueError('complete local tool accounting must match the preserved known records')
        return self


class QualityStartV2(QualityStartV1):
    schema_version: Literal[2] = 2
    policy_binding_sha256: SHA256


class QualityExecutionV2(QualityExecutionV1):
    schema_version: Literal[2] = 2
    policy_binding_sha256: SHA256


class QualitySecondaryRecordV1(VersionedContract):
    kind: Literal['a4_quality_secondary_record'] = 'a4_quality_secondary_record'
    sample_id: Text
    input_fingerprint: SHA256
    secondary_policy_sha256: SHA256
    tool_sha256: SHA256
    status: Literal['reviewed', 'not_run', 'failed', 'cancelled']
    primary_report: AuditReportV3 | None
    review: SupplementalReviewV1 | None
    attempts: tuple[VisionAttemptV1 | OCRAttemptV1, ...]

    @model_validator(mode='after')
    def completed_review(self):
        if (self.status == 'reviewed') != (self.review is not None):
            raise ValueError('completed secondary record requires its actual review')
        if self.review is not None and self.primary_report is None:
            raise ValueError('completed secondary record requires its actual primary report')
        if self.review is not None and (self.review.sample_id != self.sample_id
                or self.review.input_fingerprint != self.input_fingerprint
                or any(x.backend != ('apple_vision' if isinstance(x.attempt,VisionAttemptV1) else 'tesseract')
                       for x in self.review.ocr)):
            raise ValueError('secondary quality review identity/backend differs')
        return self


class QualityCasePolicyV1(VersionedContract):
    kind: Literal['a4_quality_case_policy'] = 'a4_quality_case_policy'
    policy_binding_sha256: SHA256
    planned_execution_policy_sha256: SHA256
    effective_execution_policy_sha256: SHA256 | None
    sample_id: Text
    input_fingerprint: SHA256
    index: int = Field(ge=0)
    remaining_model_attempts: int = Field(ge=0)
    effective_max_attempts: Literal[1, 2] | None

    @field_validator('index', 'remaining_model_attempts', 'effective_max_attempts', mode='before')
    @classmethod
    def exact_integers(cls, value):
        if value is not None and type(value) is not int:
            raise ValueError('case policy counts must be integers')
        return value


class QualityToolJournalV1(VersionedContract):
    kind: Literal['a4_quality_tool_journal'] = 'a4_quality_tool_journal'
    sample_id: Text
    input_fingerprint: SHA256
    secondary_policy_sha256: SHA256
    tool_sha256: SHA256
    logical_invocations: int = Field(ge=1,le=8)
    state: Literal['active','settled','uncertain']
    attempts: tuple[VisionAttemptV1 | OCRAttemptV1,...]

    @model_validator(mode='after')
    def count_binding(self):
        if (len(self.attempts) > self.logical_invocations
                or (self.state == 'settled') != (len(self.attempts) == self.logical_invocations)):
            raise ValueError('tool journal state must match completed bounded observations')
        return self


class QualityVisionOCR(VisionOCR):
    """Fixed native observation wrapper; callback only persists invocation state."""
    def __init__(self,config,*,journal_writer):
        super().__init__(config)
        self.logical_invocations,self.journal_writer = 0,journal_writer

    async def observe(self,current,crop,budget):
        self.logical_invocations += 1
        self.journal_writer('active',current,budget,self.logical_invocations)
        try:
            return await super().observe(current,crop,budget)
        finally:
            self.journal_writer('settled' if len(budget.attempts) == self.logical_invocations else 'uncertain',
                                current,budget,self.logical_invocations)


class QualityTesseractOCR(TesseractOCR):
    """Fixed two-process native observation wrapper with durable state recording."""
    def __init__(self,config,*,journal_writer):
        super().__init__(config)
        self.logical_invocations,self.journal_writer = 0,journal_writer

    async def observe(self,current,crop,budget):
        self.logical_invocations += 1
        self.journal_writer('active',current,budget,self.logical_invocations)
        try:
            return await super().observe(current,crop,budget)
        finally:
            self.journal_writer('settled' if len(budget.attempts) == self.logical_invocations else 'uncertain',
                                current,budget,self.logical_invocations)


class QualityEvidenceReviewer(EvidenceReviewer):
    """Fixed primary capture; use the existing native supplementary algorithm."""
    primary_report: AuditReportV3 | None = None
    frozen_identity_policy: SecondaryReviewConfigV1

    async def __call__(self,current,observed):
        self.primary_report = observed
        return await super().__call__(current,observed)


# Function-object references let identity checks reject per-instance/subclass
# overrides without equating a mutable method name with its implementation.
QUALITY_VISION_OBSERVE = QualityVisionOCR.observe
QUALITY_TESSERACT_OBSERVE = QualityTesseractOCR.observe
QUALITY_REVIEW_CALL = QualityEvidenceReviewer.__call__
NATIVE_VISION_OBSERVE = VisionOCR.observe
NATIVE_TESSERACT_OBSERVE = TesseractOCR.observe
NATIVE_REVIEW_CALL = EvidenceReviewer.__call__


def _quality_policy_hash(manifest: FrozenQualityManifestV2) -> str:
    return fingerprint({
        'validation_path': manifest.validation_path, 'execution_mode':manifest.execution_mode,
        'experiment_identity': manifest.experiment_identity.identity_sha256,
        'capability': manifest.capability_sha256, 'language': manifest.language_profile_sha256,
        'secondary': manifest.secondary_policy_sha256, 'tool': manifest.secondary_tool_sha256,
        'endpoint_family': manifest.endpoint_family, 'region': manifest.region,
    })


def _secondary_policy(path) -> SecondaryReviewConfigV1 | None:
    return _parse(_read(_safe_path(Path(path))), SecondaryReviewConfigV1) if path is not None else None


def _freeze_vision_tool(policy: SecondaryReviewConfigV1) -> FrozenVisionToolV1:
    if policy.backend != 'apple_vision':
        raise InputError('secondary_backend_producer_not_frozen; no primary-only fallback')
    helper, _ = read_vision_helper(Path(policy.apple_vision.helper_directory))
    config = policy.apple_vision
    if not set(config.languages).issubset(helper.supported_languages):
        raise InputError('secondary_language_unverified')
    safe = policy.model_dump(mode='json')
    safe['apple_vision'].pop('helper_directory')
    fields = dict(schema_version=1, kind='a4_frozen_vision_tool', helper=helper.model_dump(mode='json'),
        producer=helper.producer.model_copy(update={'languages':config.languages}).model_dump(mode='json'),
        config_sha256=fingerprint(config.model_dump(mode='json')), secondary_policy_sha256=supplemental_policy_fingerprint(policy),
        safe_policy=safe)
    return _parse(_json_bytes({**fields, 'tool_sha256':fingerprint(fields)}), FrozenVisionToolV1)


def parse_frozen_quality_tool(raw: bytes) -> FrozenVisionToolV1 | FrozenTesseractToolV1:
    """Pure typed artifact parsing. Never access a current helper or run a tool."""
    try:
        value = _decode_json(raw)
        cls = {'a4_frozen_vision_tool':FrozenVisionToolV1,
               'a4_frozen_tesseract_tool':FrozenTesseractToolV1}.get(value.get('kind')) if isinstance(value,dict) else None
        if cls is None:
            raise ValueError
    except (ValueError,TypeError,UnicodeError):
        raise InputError('unsupported strict frozen quality tool kind') from None
    return _parse(raw,cls)


def frozen_secondary_policy(tool):
    """Pure archived-region/limit view, without private runtime locators."""
    from .evidence_review import OCRRegionV1
    if isinstance(tool,FrozenTesseractToolV1):
        safe = tool.safe_policy
        return SimpleNamespace(backend='tesseract',regions=safe.regions,max_tool_attempts=safe.max_tool_attempts,
            tool_timeout_seconds=safe.tool_timeout_seconds,tesseract=SimpleNamespace(max_words=safe.max_words,
                stdout_max_bytes=safe.stdout_max_bytes,stderr_max_bytes=safe.stderr_max_bytes),apple_vision=None)
    if isinstance(tool,FrozenVisionToolV1):
        safe = tool.safe_policy
        return SimpleNamespace(backend='apple_vision',regions=tuple(OCRRegionV1.model_validate_json(json.dumps(x))
            for x in safe['regions']),max_tool_attempts=safe['max_tool_attempts'],
            tool_timeout_seconds=safe['tool_timeout_seconds'],tesseract=None,
            apple_vision=SimpleNamespace(max_lines=safe['apple_vision']['max_lines']))
    raise InputError('controlled frozen quality tool required')


def _planned_execution_hash(plan, endpoint_family, region, *, limit=None,client_policy='per_request'):
    from .runtime import StandaloneExecutor
    from .telemetry import execution_policy_fingerprint
    if client_policy not in ('per_request','scoped_owned'):
        raise InputError('unsupported_quality_client_policy')
    provider = SimpleNamespace(_session=None if client_policy == 'per_request' else SimpleNamespace(_borrowed=None),
                               endpoint_family=endpoint_family, region=region)
    effective = plan.max_attempts_per_audit if limit is None else limit
    executor = StandaloneExecutor(effective, request_timeout=plan.request_timeout, total_timeout=plan.total_timeout)
    return execution_policy_fingerprint(provider,executor,max_retries=effective-1)


def _manifest_client_policy(manifest) -> str:
    for selected in ('per_request','scoped_owned'):
        if manifest.experiment_identity.execution_policy_sha256 == _planned_execution_hash(
                manifest.plan,manifest.endpoint_family,manifest.region,client_policy=selected):
            if selected == 'scoped_owned' and manifest.execution_mode != 'live':
                raise ValueError('scoped quality client requires actual live provider selection')
            return selected
    raise ValueError('quality execution policy does not match either supported client lifecycle')


def _quality_identity(config, capability, language, secondary, plan, endpoint_family, region, execution_mode,
                      client_policy='per_request'):
    from .environment import collect_environment
    return build_experiment_identity(prompt_version=PROMPT_VERSION,
        prompt_bytes=files('poster_critic').joinpath('prompts/critic_v4.txt').read_bytes(),
        provider_config=config, capability=capability, language_profile=language, supplemental_policy=secondary,
        environment=collect_environment(scope='standalone',require_live=execution_mode == 'live'),
        execution_policy_sha256=_planned_execution_hash(plan,endpoint_family,region,client_policy=client_policy))


def _software_snapshot() -> dict[str, bytes]:
    """Inventory A4 code and declared static resources, never private config/state."""
    contents = {}
    def visit(node, prefix=''):
        for child in sorted(node.iterdir(), key=lambda item: item.name):
            name = prefix + child.name
            if isinstance(child, Path) and child.is_symlink():
                raise InputError('A4 quality software inventory contains a symlink')
            if child.is_dir() and child.name != '__pycache__':
                visit(child, name + '/')
            elif child.is_file():
                resource = name.split('/')[0] in ('prompts', 'resources', 'templates')
                code = name.endswith(('.py', '.js', '.swift'))
                if not resource and not code:
                    continue
                if child.name.startswith('.') or child.name.endswith(('.sqlite', '.db', '.env', '.pyc')):
                    raise InputError('private/state files cannot enter the quality software inventory')
                raw = child.read_bytes()
                # Provider templates have only environment-variable names, not keys.
                if resource and child.name.endswith('.json'):
                    if not child.name.endswith('.example.json'):
                        raise InputError('only typed safe example JSON is a quality software resource')
                    value = _decode_json(raw)
                    if not isinstance(value, dict):
                        raise InputError('safe quality resource must be an object')
                    _parse(raw, ModelCatalog if value.get('schema_version') == 2 else ProviderConfig)
                contents[name] = raw
    try:
        visit(files('poster_critic'))
    except (OSError, ValueError):
        raise InputError('A4 quality software inventory unavailable') from None
    if 'quality_validation.py' not in contents or 'prompts/critic_v4.txt' not in contents:
        raise InputError('A4 quality software inventory incomplete')
    return contents


def _inventory_hash(contents):
    return fingerprint([{'path': name, 'sha256': hashlib.sha256(raw).hexdigest(),
                         'size_bytes': len(raw)} for name, raw in sorted(contents.items())])


def _environment():
    result = {'python': platform.python_version(), 'implementation': platform.python_implementation()}
    for package in ('pydantic', 'Pillow', 'httpx'):
        try:
            result[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            result[package] = None
    return result


def _config(path):
    if path is None:
        return None
    return _parse(_read(_safe_path(Path(path))), ProviderConfig)


def _versioned_quality(raw, legacy, current):
    try:
        value = _decode_json(raw)
        if not isinstance(value,dict):
            raise ValueError
        version = value.get('schema_version',1)
        if type(version) is not int:
            raise ValueError
    except (ValueError,UnicodeError,TypeError):
        raise InputError('invalid strict quality versioned object') from None
    return _parse(raw,current if version == 2 else legacy)


def _new_destination(path):
    out = Path(path).expanduser().absolute()
    if any(part.is_symlink() for part in (out, *out.parents)) or out.exists():
        raise InputError('quality destination must be new and contain no symlinks')
    return out


def freeze_quality_validation(dataset_path: str | Path, output_dir: str | Path, *,
                              label_source: str | Path, threshold_path: str | Path,
                              label_kind: str = 'unverified', label_reference: str = 'provided_label_sheet',
                              agreement_source: str | Path | None = None,
                              config_path: str | Path | None = None, repeats: int = 2,
                              max_attempts: int = 2, max_total_attempts: int = 48,
                              request_timeout: float = 30.0, total_timeout: float = 90.0,
                              _p2: dict | None = None) -> FrozenQualityManifestV1 | FrozenQualityManifestV2:
    """Freeze sources, actual supplied labels/thresholds and an audit-only plan."""
    plan = QualityPlanV1(repeats=repeats, max_attempts_per_audit=max_attempts,
        max_total_attempts=max_total_attempts, request_timeout=request_timeout, total_timeout=total_timeout)
    dataset, contents, bindings, _ = _prepare_dataset(Path(dataset_path), label_source=Path(label_source))
    threshold = _parse(_read(_safe_path(Path(threshold_path))), ThresholdProfileV1)
    selected = [s for s in dataset.samples if s.split == 'validation']
    if (not selected or threshold.validation_samples != len(selected) or threshold.repeats != repeats
            or not any(s.expected_decision == 'pass' for s in selected)
            or not any(s.expected_decision == 'fail' for s in selected)):
        raise InputError('quality plan must match threshold design and include normal/problem validation cases')
    agreement = None
    if threshold.state == 'agreed':
        if agreement_source is None:
            raise InputError('agreed profile needs its actual agreement source file')
        agreement = _read(_safe_path(Path(agreement_source)), limit=256 * 1024)
        if not agreement.strip() or hashlib.sha256(agreement).hexdigest() != threshold.agreement_evidence_sha256:
            raise InputError('agreement evidence hash differs or evidence is empty')
    elif agreement_source is not None:
        raise InputError('draft profile does not accept an agreement source')
    # The software-only legacy freezer rewrites purpose; this separate version retains it.
    value = _decode_json(contents['dataset.json'][0])
    value['purpose'] = dataset.purpose
    contents['dataset.json'] = (_json_bytes(value), 'dataset')
    contents['thresholds.json'] = (_json_bytes(threshold.model_dump(mode='json')), 'thresholds')
    if agreement is not None:
        contents['agreement.bin'] = (agreement, 'agreement')
    config = _config(config_path)
    if config is not None:
        contents['config.json'] = (_json_bytes(config.model_dump(mode='json')), 'config')
    if _p2 is not None:
        if _p2['capability'] is not None:
            contents['capability.json'] = (_json_bytes(_p2['capability'].model_dump(mode='json')), 'capability')
        contents['language.json'] = (_json_bytes(_p2['language'].model_dump(mode='json')), 'language')
        if _p2['tool'] is not None:
            contents['tool_identity.json'] = (_json_bytes(_p2['tool'].model_dump(mode='json')), 'tool_identity')
    source = _software_snapshot()
    for name, raw in source.items():
        contents['software/' + name] = (raw, 'software')
    labels = _parse(contents['labels.json'][0], LabelSheet)
    sample_ids = {s.sample_id for s in selected}
    version = 1 if _p2 is None else 2
    body = dict(schema_version=version, kind='a4_frozen_quality_validation', source_purpose=dataset.purpose,
        label_kind=label_kind, label_reference=label_reference, annotator=labels.annotator,
        threshold_state=threshold.state, threshold_profile_sha256=fingerprint(threshold.model_dump(mode='json')),
        prompt_version=PROMPT_VERSION, software_fingerprint_v1=software_fingerprint(),
        source_inventory_sha256=_inventory_hash(source), environment=_environment(),
        provider_config_sha256=fingerprint(config.model_dump(mode='json')) if config is not None else None,
        plan=plan.model_dump(mode='json'), samples=[dict(schema_version=1, **s.model_dump(mode='json'))
            for s in bindings if s.sample_id in sample_ids],
        artifacts=[dict(schema_version=version, path=name, sha256=hashlib.sha256(raw).hexdigest(),
                        size_bytes=len(raw), kind=kind) for name, (raw, kind) in sorted(contents.items())])
    if _p2 is not None:
        identity = _quality_identity(config,_p2['capability'],_p2['language'],_p2['secondary'],plan,
                                     _p2['endpoint_family'],_p2['region'],_p2['execution_mode'],_p2['client_policy'])
        body.update(validation_path=_p2['validation_path'],execution_mode=_p2['execution_mode'],
            experiment_identity=identity.model_dump(mode='json'), capability_sha256=identity.capability_sha256,
            language_profile_sha256=identity.language_profile_sha256, secondary_policy_sha256=identity.supplemental_policy_sha256,
            secondary_tool_sha256=_p2['tool'].tool_sha256 if _p2['tool'] is not None else None,
            endpoint_family=_p2['endpoint_family'],region=_p2['region'])
    manifest = _parse(_json_bytes(dict(body, seal_sha256=fingerprint(body))),
                      FrozenQualityManifestV1 if _p2 is None else FrozenQualityManifestV2)
    out = _new_destination(output_dir)
    out.mkdir(parents=True)
    write_json(out / '.incomplete.json', {'kind': 'a4_incomplete_quality_freeze', 'network_requests': 0})
    for name, (raw, _) in sorted(contents.items()):
        write_bytes(out / name, raw)
    write_json(out / 'manifest.json', manifest.model_dump(mode='json'))
    (out / '.incomplete.json').unlink()
    return manifest


def _quality_v2_selection(*,capability_path,language_profile_path,secondary_policy_path,validation_path,
                          execution_mode,endpoint_family,region,client_policy,config_path):
    if validation_path not in ('primary_only','primary_plus_local_ocr') or execution_mode not in ('fake','live'):
        raise InputError('invalid explicit quality policy path or execution mode')
    if validation_path == 'primary_only' and secondary_policy_path is not None:
        raise InputError('primary_only_cannot_claim_secondary_policy')
    if validation_path != 'primary_only' and secondary_policy_path is None:
        raise InputError('selected_secondary_policy_required')
    if client_policy not in ('per_request','scoped_owned') or client_policy == 'scoped_owned' and execution_mode != 'live':
        raise InputError('unsupported_quality_client_policy_or_mode')
    config = _config(config_path)
    capability = load_capability(capability_path) if capability_path is not None else None
    language = read_language_profile(language_profile_path) if language_profile_path is not None else legacy_language_profile()
    secondary = _secondary_policy(secondary_policy_path)
    if execution_mode == 'live' and (config is None or capability is None):
        raise InputError('new_live_quality_requires_explicit_config_and_capability')
    if capability is not None and (config is None or config.profile != capability.provider_profile
            or config.model != capability.model_id or config.base_url not in capability.allowed_base_urls
            or endpoint_family != capability.endpoint_family or region != capability.region):
        raise InputError('quality_capability_mismatch')
    return dict(capability=capability,language=language,secondary=secondary,
        validation_path=validation_path,execution_mode=execution_mode,endpoint_family=endpoint_family,
        region=region,client_policy=client_policy)


def freeze_quality_validation_v2(dataset_path, output_dir, *, capability_path=None, language_profile_path=None,
                                 secondary_policy_path=None, validation_path='primary_only', execution_mode='fake',
                                 endpoint_family='chat_completions', region=None,client_policy='per_request', **kwargs) -> FrozenQualityManifestV2:
    p2 = _quality_v2_selection(capability_path=capability_path,language_profile_path=language_profile_path,
        secondary_policy_path=secondary_policy_path,validation_path=validation_path,execution_mode=execution_mode,
        endpoint_family=endpoint_family,region=region,client_policy=client_policy,config_path=kwargs.get('config_path'))
    secondary = p2['secondary']
    if secondary is not None and secondary.backend == 'tesseract':
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            p2['tool'] = asyncio.run(freeze_tesseract_tool(secondary))
        else:
            raise InputError('running_loop_requires_async_freeze_quality_validation_v2')
    else:
        p2['tool'] = _freeze_vision_tool(secondary) if secondary else None
    return freeze_quality_validation(dataset_path,output_dir,_p2=p2,**kwargs)


async def async_freeze_quality_validation_v2(dataset_path,output_dir,*,capability_path=None,language_profile_path=None,
        secondary_policy_path=None,validation_path='primary_only',execution_mode='fake',endpoint_family='chat_completions',
        region=None,client_policy='per_request',**kwargs) -> FrozenQualityManifestV2:
    p2 = _quality_v2_selection(capability_path=capability_path,language_profile_path=language_profile_path,
        secondary_policy_path=secondary_policy_path,validation_path=validation_path,execution_mode=execution_mode,
        endpoint_family=endpoint_family,region=region,client_policy=client_policy,config_path=kwargs.get('config_path'))
    secondary = p2['secondary']
    p2['tool'] = (await freeze_tesseract_tool(secondary) if secondary.backend == 'tesseract'
                  else _freeze_vision_tool(secondary)) if secondary else None
    return freeze_quality_validation(dataset_path,output_dir,_p2=p2,**kwargs)


def verify_frozen_quality(bundle_dir: str | Path, *, config_path: str | Path | None = None,
                          capability_path=None, language_profile_path=None,
                          secondary_policy_path=None) -> FrozenQualityManifestV1 | FrozenQualityManifestV2:
    """Verify exact bytes, source inventory, environment, labels, plan and config."""
    root = _safe_path(Path(bundle_dir), file=False)
    raw = _read(_reference(root, 'manifest.json'))
    manifest = _versioned_quality(raw,FrozenQualityManifestV1,FrozenQualityManifestV2)
    if not isinstance(manifest, FrozenQualityManifestV2) and any(
            item is not None for item in (capability_path, language_profile_path, secondary_policy_path)):
        raise InputError('legacy_quality_cannot_claim_new_policy_bindings; freeze a new V2 bundle')
    actual = set()
    for path in root.rglob('*'):
        if path.is_symlink():
            raise InputError('quality bundle contains a symlink')
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    inventory = {a.path: a for a in manifest.artifacts}
    if actual != set(inventory) | {'manifest.json'}:
        raise InputError('quality bundle incomplete or inventory differs')
    for name, item in inventory.items():
        raw = _read(_reference(root, name), limit=20 * 1024 * 1024)
        if len(raw) != item.size_bytes or hashlib.sha256(raw).hexdigest() != item.sha256:
            raise InputError('quality artifact hash differs')
    current = _software_snapshot()
    if (_inventory_hash(current) != manifest.source_inventory_sha256
            or software_fingerprint() != manifest.software_fingerprint_v1
            or PROMPT_VERSION != manifest.prompt_version or _environment() != manifest.environment):
        raise InputError('current quality source/prompt/environment differs from frozen plan')
    frozen_source = {name.removeprefix('software/'): _read(root / name) for name in inventory if name.startswith('software/')}
    if frozen_source != current:
        raise InputError('quality source inventory differs')
    threshold = _parse(_read(_reference(root, 'thresholds.json')), ThresholdProfileV1)
    if (fingerprint(threshold.model_dump(mode='json')) != manifest.threshold_profile_sha256
            or threshold.state != manifest.threshold_state):
        raise InputError('quality threshold binding differs')
    if threshold.state == 'agreed':
        agreement = _read(_reference(root, 'agreement.bin'), limit=256 * 1024)
        if hashlib.sha256(agreement).hexdigest() != threshold.agreement_evidence_sha256 or not agreement.strip():
            raise InputError('quality agreement evidence differs')
    config = _config(config_path)
    expected_config = _config(root / 'config.json') if 'config.json' in inventory else None
    if config != expected_config or ((fingerprint(config.model_dump(mode='json')) if config else None)
                                    != manifest.provider_config_sha256):
        raise InputError('provide the matching explicit current quality provider config')
    dataset, prepared, bindings, _ = _prepare_dataset(root / 'dataset.json', label_source=root / 'labels.json')
    selected = {s.sample_id for s in dataset.samples if s.split == 'validation'}
    expected = tuple(QualitySampleBindingV1(**s.model_dump()) for s in bindings if s.sample_id in selected)
    labels = _parse(_read(root / 'labels.json'), LabelSheet)
    if (dataset.purpose != manifest.source_purpose or expected != manifest.samples
            or labels.annotator != manifest.annotator or threshold.validation_samples != len(selected)
            or threshold.repeats != manifest.plan.repeats):
        raise InputError('quality labels, sample binding or design differs')
    expected_kinds = {name: kind for name, (_, kind) in prepared.items()}
    expected_kinds.update({'software/' + name: 'software' for name in current})
    expected_kinds['thresholds.json'] = 'thresholds'
    if expected_config is not None:
        expected_kinds['config.json'] = 'config'
    if threshold.state == 'agreed':
        expected_kinds['agreement.bin'] = 'agreement'
    if isinstance(manifest, FrozenQualityManifestV2):
        capability = load_capability(root / 'capability.json') if manifest.capability_sha256 is not None else None
        language = read_language_profile(root / 'language.json')
        if capability_path is not None and load_capability(capability_path) != capability:
            raise InputError('current quality capability differs from its frozen profile')
        if language_profile_path is not None and read_language_profile(language_profile_path) != language:
            raise InputError('current quality language differs from its frozen profile')
        secondary = _secondary_policy(secondary_policy_path)
        if manifest.validation_path == 'primary_only' and secondary is not None:
            raise InputError('primary_only_cannot_claim_secondary_policy')
        if manifest.validation_path == 'primary_plus_local_ocr' and secondary is None:
            raise InputError('selected_secondary_policy_required')
        if secondary is not None:
            frozen_tool = parse_frozen_quality_tool(_read(root/'tool_identity.json'))
            current_tool = (validate_frozen_tesseract_tool(secondary,frozen_tool) if secondary.backend == 'tesseract'
                            else _freeze_vision_tool(secondary))
            if current_tool != frozen_tool or frozen_tool.tool_sha256 != manifest.secondary_tool_sha256:
                raise InputError('current quality secondary policy or actual tool producer differs')
            expected_kinds['tool_identity.json'] = 'tool_identity'
        if capability is not None:
            expected_kinds['capability.json'] = 'capability'
        expected_kinds['language.json'] = 'language'
        identity = _quality_identity(config,capability,language,secondary,manifest.plan,
                                     manifest.endpoint_family,manifest.region,manifest.execution_mode,_manifest_client_policy(manifest))
        if identity != manifest.experiment_identity:
            raise InputError('current quality experiment identity differs from the frozen policy')
        if manifest.execution_mode == 'live' and (config is None or capability is None):
            raise InputError('new_live_quality_requires_explicit_config_and_capability')
        if capability is not None and (config is None or config.profile != capability.provider_profile
                or config.model != capability.model_id or config.base_url not in capability.allowed_base_urls
                or manifest.endpoint_family != capability.endpoint_family or manifest.region != capability.region):
            raise InputError('quality_capability_mismatch')
    if expected_kinds != {name: item.kind for name, item in inventory.items()}:
        raise InputError('quality artifact kinds differ')
    return manifest


def _ratio(numerator, denominator):
    return {'numerator': numerator, 'denominator': denominator,
            'rate': numerator / denominator if denominator else None}


def planned_quality_metrics(jobs: list[dict], rows: dict[int, dict]) -> dict:
    """Missing/error slots stay in every applicable planned denominator."""
    normals = [i for i, job in enumerate(jobs) if job['expected_decision'] == 'pass']
    problems = [i for i, job in enumerate(jobs) if job['expected_decision'] == 'fail']
    grouped = {}
    for index, job in enumerate(jobs):
        grouped.setdefault(job['sample_id'], []).append(index)
    repeated = [indices for indices in grouped.values() if len(indices) == 2]
    row = lambda i: rows.get(i, {})
    return {
        'decision_agreement': _ratio(sum(row(i).get('decision') == j['expected_decision']
                                           for i, j in enumerate(jobs)), len(jobs)),
        'problem_detection': _ratio(sum(row(i).get('decision') == 'fail'
            and row(i).get('expected_issues_found', False) for i in problems), len(problems)),
        'problem_false_pass': _ratio(sum(row(i).get('decision') == 'pass' for i in problems), len(problems)),
        'normal_false_fail': _ratio(sum(row(i).get('decision') == 'fail' for i in normals), len(normals)),
        'unknown_or_execution_failure': _ratio(sum(row(i).get('decision') in (None, 'unknown')
                                                    for i in range(len(jobs))), len(jobs)),
        'first_valid_response': _ratio(sum(bool(row(i).get('first_valid')) for i in range(len(jobs))), len(jobs)),
        'repeat_decision_consistency': _ratio(sum(row(a).get('decision') is not None
            and row(a).get('decision') == row(b).get('decision') for a, b in repeated), len(repeated)),
    }


def evaluate_quality_gates(profile: ThresholdProfileV1, metrics: dict, per_repeat: dict) -> tuple[dict, ...]:
    """Evaluate supplied numeric rules only; this helper makes no quality claim."""
    profile = ThresholdProfileV1.model_validate_json(profile.model_dump_json())
    result = []
    for gate in profile.gates:
        selected = {'all': metrics} if gate.scope == 'all' else per_repeat
        checks = []
        for scope, values in selected.items():
            metric = values[gate.metric]
            value = metric['rate']
            passed = (value is not None and metric['denominator'] > 0
                and (gate.minimum_rate is None or value >= gate.minimum_rate)
                and (gate.maximum_rate is None or value <= gate.maximum_rate))
            checks.append({'scope': scope, **metric, 'passed': passed})
        result.append({'metric': gate.metric, 'scope': gate.scope,
            'minimum_rate': gate.minimum_rate, 'maximum_rate': gate.maximum_rate,
            'passed': bool(checks) and all(c['passed'] for c in checks), 'checks': checks})
    return tuple(result)


def _validate_run_spec(root, manifest, run):
    spec = run['spec']
    if (type(spec.get('live')) is not bool or type(spec.get('repeats')) is not int
            or type(spec.get('max_total_attempts')) is not int
            or type(spec.get('max_attempts_per_audit')) is not int):
        raise InputError('saved quality run uses invalid plan field types')
    dataset = _parse(_read(root / 'dataset.json'), Dataset)
    _, _, full_bindings, _ = _prepare_dataset(root / 'dataset.json', label_source=root / 'labels.json')
    if spec.get('input_hashes') != {s.sample_id: s.input_sha256 for s in full_bindings}:
        raise InputError('saved quality run full input binding differs')
    jobs = []
    binding = {s.sample_id: s for s in manifest.samples}
    for repeat in range(1, manifest.plan.repeats + 1):
        for sample in dataset.samples:
            if sample.split != 'validation':
                continue
            b = binding[sample.sample_id]
            jobs.append(dict(sample.model_dump(mode='json'), repeat=repeat,
                image_sha256=b.image_sha256, input_sha256=b.input_sha256,
                response_sha256=None if spec.get('live') or sample.response_path is None
                    else hashlib.sha256(_read(_reference(root, sample.response_path))).hexdigest()))
    plan = manifest.plan
    if (spec.get('jobs') != jobs or spec.get('dataset_sha256') != hashlib.sha256(_read(root / 'dataset.json')).hexdigest()
            or spec.get('split') != 'validation' or spec.get('repeats') != plan.repeats
            or spec.get('max_total_attempts') != plan.max_total_attempts
            or spec.get('max_attempts_per_audit') != plan.max_attempts_per_audit
            or spec.get('request_timeout') != plan.request_timeout or spec.get('total_timeout') != plan.total_timeout
            or spec.get('software_sha256') != manifest.software_fingerprint_v1
            or spec.get('prompt_version') != manifest.prompt_version or spec.get('purpose') != dataset.purpose):
        raise InputError('saved quality run differs from the frozen plan')
    if spec.get('live'):
        config = _config(root / 'config.json') if manifest.provider_config_sha256 is not None else None
        if (config is None or spec.get('provider_config') != config.model_dump(mode='json')
                or spec.get('provider') != f'{config.profile}/{config.model}'
                or spec.get('evidence_kind') != ('live_model_evaluation' if dataset.purpose == 'human_labeled' else 'software_demo_only')):
            raise InputError('saved live quality provider binding differs')
    elif spec.get('provider') != 'fake-fixed-v1' or spec.get('provider_config') is not None:
        raise InputError('saved offline quality provider binding differs')
    if isinstance(manifest, FrozenQualityManifestV2):
        expected = {'quality_protocol_version':2, 'policy_binding_sha256':_quality_policy_hash(manifest),
            'experiment_identity_sha256':manifest.experiment_identity.identity_sha256,
            'validation_path':manifest.validation_path, 'execution_mode':manifest.execution_mode,
            'claim_scope':'selected_audit_path_only'}
        if (any(spec.get(k) != v for k,v in expected.items())
                or spec['live'] != (manifest.execution_mode == 'live')
                or spec.get('client_policy','per_request') != _manifest_client_policy(manifest)):
            raise InputError('saved quality run selected path, mode or policy binding differs')
        _saved_benchmark_cases(spec,jobs)


def _run_artifacts(out, jobs, *, manifest=None):
    artifacts = []
    permitted = {'run.json', 'summary.json', 'results.jsonl', '.run.lock'} | {
        f'{i:04d}{suffix}' for i in range(len(jobs)) for suffix in ('.json', '.result.json', '.pending.json')}
    if isinstance(manifest, FrozenQualityManifestV2):
        permitted.update(f'{i:04d}.policy.json' for i in range(len(jobs)))
        permitted.update(f'{i:04d}.telemetry.json' for i in range(len(jobs)))
        if manifest.validation_path == 'primary_plus_local_ocr':
            permitted.update(f'{i:04d}.evidence.json' for i in range(len(jobs)))
            permitted.update(f'{i:04d}.tool.pending.json' for i in range(len(jobs)))
    for path in out.rglob('*'):
        if path.is_symlink() or path.is_dir() or path.name not in permitted:
            raise InputError('quality run has symlinks, nested or unplanned artifacts')
        if path.name == '.run.lock':
            continue
        raw = _read(path, limit=20 * 1024 * 1024)
        artifacts.append(QualityArtifactV1(path=path.name, sha256=hashlib.sha256(raw).hexdigest(),
                                            size_bytes=len(raw), kind='observation'))
    return sorted(artifacts, key=lambda a: a.path)


def _artifacts_hash(artifacts):
    return fingerprint([item.model_dump(mode='json') for item in artifacts])


def _attempt_number(attempt):
    return attempt.attempt if isinstance(attempt,VisionAttemptV1) else attempt.attempt_index


def _attempt_starts(attempt):
    return attempt.process_starts if isinstance(attempt,VisionAttemptV1) else attempt.actual_process_starts


def _review_from_attempts(audit, policy, attempts):
    """Recompute the deterministic selected OCR branch from bound saved attempts."""
    from .checks import text_matches
    from .evidence_review import EvidenceGapV1, OCRReviewRecordV1
    from .identity import input_fingerprint
    copies = {x.copy_id:x for x in audit.approved_copy or ()}
    records, gaps = [], []
    for region, attempt in zip(policy.regions, attempts, strict=True):
        records.append(OCRReviewRecordV1(copy_id=region.copy_id,region_bbox_xyxy=region.bbox_xyxy,
                                        backend=policy.backend,attempt=attempt))
        if attempt.status != 'observed':
            doubt, missing, code = f'OCR did not complete: {attempt.error_code}.', 'A bounded actual OCR observation.', 'ocr_unavailable'
            action = (getattr(attempt,'suggested_action',None) or
                'Verify the explicitly selected helper, language support and bounded tool deadline; retain the candidate and do not retry or regenerate blindly.')
        else:
            if isinstance(attempt,VisionAttemptV1):
                parts = sorted(attempt.observation.lines,key=lambda x:(x.crop_bbox_xyxy[1],x.crop_bbox_xyxy[0]))
                text = '\n'.join(x.text for x in parts)
                reliable = bool(parts) and all(x.confidence >= region.minimum_confidence for x in parts)
            else:
                parts = sorted(attempt.observation.words,key=lambda x:(x.block_num,x.par_num,x.line_num,x.word_num))
                grouped = {}
                for word in parts:
                    grouped.setdefault((word.block_num,word.par_num,word.line_num),[]).append(word.text)
                text = '\n'.join(' '.join(words) for words in grouped.values())
                reliable = bool(parts) and all(x.text and x.confidence is not None and x.confidence/100 >= region.minimum_confidence for x in parts)
            if reliable and text_matches(text,copies[region.copy_id]):
                continue
            doubt = 'OCR text is missing, uncertain or differs from the approved wording; this is not a confirmed image defect.'
            missing, code = 'Consistent pixel transcription for this explicitly selected text region.', 'ocr_text_uncertain'
            action = 'Have A1 verify this region and current text evidence before changing exact copy; do not regenerate blindly.'
        gaps.append(EvidenceGapV1(check_id='text_accuracy',subject_id=region.copy_id,code=code,
            required_for_release=region.required_for_release,doubt=doubt,missing_evidence=missing,suggestion=action))
    return SupplementalReviewV1(sample_id=audit.sample_id,image_sha256=audit.image.sha256,
        input_fingerprint=input_fingerprint(audit),renderer=None,ocr=tuple(records),findings=(),
        gaps=tuple(gaps),proposed_hints=())


def _verify_v2_run_evidence(root, out, manifest, run, rows, secondary):
    from .adapters.local_input import load_input
    from .evidence import crop_evidence
    from .evidence_review import _checked_attempt, apply_supplemental_review
    from .dispatch import _bound_report
    from .evaluation_io import artifact_calls
    starts = used = previous_calls = unresolved = 0
    selected_cases = _saved_benchmark_cases(run['spec'],run['spec']['jobs'])
    tool = parse_frozen_quality_tool(_read(root/'tool_identity.json')) if secondary else None
    for index, job in enumerate(run['spec']['jobs']):
        policy_path = out / f'{index:04d}.policy.json'
        if not policy_path.exists():
            if index in rows and (out/f'{index:04d}.result.json').exists():
                raise InputError('completed V2 quality audit lacks its actual case policy')
            continue
        remaining = max(0,manifest.plan.max_total_attempts-previous_calls)
        limit = min(manifest.plan.max_attempts_per_audit,remaining) if remaining else None
        expected = QualityCasePolicyV1(policy_binding_sha256=_quality_policy_hash(manifest),
            planned_execution_policy_sha256=manifest.experiment_identity.execution_policy_sha256,
            effective_execution_policy_sha256=_planned_execution_hash(manifest.plan,manifest.endpoint_family,
                manifest.region,limit=limit,client_policy=_manifest_client_policy(manifest)) if limit else None,
            sample_id=job['sample_id'],input_fingerprint=job['input_sha256'],index=index,
            remaining_model_attempts=remaining,effective_max_attempts=limit)
        if _parse(_read(policy_path),QualityCasePolicyV1) != expected:
            raise InputError('actual case execution policy differs from the frozen cumulative budget')
        artifact_path = out/f'{index:04d}.json'
        artifact = _decode_json(_read(artifact_path)) if artifact_path.exists() else None
        if artifact is not None:
            case_calls = artifact_calls(artifact,job,run['spec'])
            if len(case_calls) > (limit or 0):
                raise InputError('actual quality case calls exceed its remaining effective budget')
            previous_calls += len(case_calls)
        elif index in rows:
            previous_calls += rows[index]['attempts']
        _verify_case_telemetry(out,index,job,manifest,expected,artifact,selected_cases.get(index),run['spec'])
        if tool is None:
            continue
        tool_journal_path = out/f'{index:04d}.tool.pending.json'
        journal = _parse(_read(tool_journal_path),QualityToolJournalV1) if tool_journal_path.exists() else None
        if journal is not None and (journal.sample_id != job['sample_id'] or journal.input_fingerprint != job['input_sha256']
                or journal.secondary_policy_sha256 != manifest.secondary_policy_sha256
                or journal.tool_sha256 != manifest.secondary_tool_sha256
                or journal.logical_invocations > len(secondary.regions)):
            raise InputError('saved pending local tool journal differs from the selected input and policy')
        sidecar_path = out/f'{index:04d}.evidence.json'
        if not sidecar_path.exists():
            if (out/f'{index:04d}.result.json').exists():
                raise InputError('completed selected quality path lacks its bounded tool evidence')
            if journal is None:
                # A primary call may have completed immediately before the tool marker.
                # Interrupted unmarked selected branches cannot prove exact zero tools.
                unresolved += int(index in rows)
                continue
            saved = None
        else:
            saved = _parse(_read(sidecar_path),QualitySecondaryRecordV1)
        if saved is not None and (saved.sample_id != job['sample_id'] or saved.input_fingerprint != job['input_sha256']
                or saved.secondary_policy_sha256 != manifest.secondary_policy_sha256
                or saved.tool_sha256 != manifest.secondary_tool_sha256
                or len(saved.attempts) > len(secondary.regions)):
            raise InputError('saved secondary evidence policy or input differs')
        if saved is not None and saved.status != 'not_run' and journal is None:
            raise InputError('executed secondary branch lacks its durable invocation journal')
        if saved is not None and journal is not None and saved.attempts != journal.attempts:
            raise InputError('completed secondary records differ from their durable tool journal')
        attempts = saved.attempts if saved is not None else journal.attempts
        if journal is not None:
            unresolved += journal.logical_invocations-len(attempts)
        audit = load_input(_reference(root,job['input_path']))
        if saved is not None and saved.primary_report is not None:
            primary = _bound_report(saved.primary_report,audit,model=run['spec']['provider'],live=run['spec']['live'])
            if (primary.prompt_version != manifest.prompt_version
                    or artifact is not None and primary.calls != artifact_calls(artifact,job,run['spec'])):
                raise InputError('secondary primary report differs from the actual model call and input binding')
        numbers = [_attempt_number(a) for a in attempts if _attempt_number(a)]
        if numbers != list(range(1,len(numbers)+1)) or len(numbers) > secondary.max_tool_attempts:
            raise InputError('saved secondary evidence exceeds its frozen tool attempt budget')
        for region, attempt in zip(secondary.regions,attempts):
            _checked_attempt(audit,crop_evidence(audit,region.bbox_xyxy),attempt,
                             backend=secondary.backend,config_sha256=tool.config_sha256)
            if _attempt_number(attempt) == 0 and (attempt.status != 'budget_refused' or _attempt_starts(attempt)):
                raise InputError('unadmitted local tool record cannot claim a process')
            observed = attempt.observation
            if observed is not None and (observed.producer != tool.producer or
                    isinstance(tool,FrozenVisionToolV1) and (observed.source_sha256 != tool.helper.source_sha256
                    or observed.executable_sha256 != tool.helper.executable_sha256)):
                raise InputError('saved local tool observation differs from the frozen actual producer')
            if observed is not None and ((isinstance(attempt,OCRAttemptV1) and len(observed.words) > secondary.tesseract.max_words)
                    or (isinstance(attempt,VisionAttemptV1) and len(observed.lines) > secondary.apple_vision.max_lines)):
                raise InputError('saved local tool observation exceeds selected item limits')
            if isinstance(attempt,OCRAttemptV1):
                if (attempt.executable_sha256 not in (None,tool.producer.executable_sha256)
                        or attempt.engine_version not in (None,tool.producer.engine_version)
                        or attempt.language_data != tool.producer.language_data[:len(attempt.language_data)]
                        or tuple(p.phase for p in attempt.processes) not in ((),('version',),('version','ocr'))
                        or any(p.stdout_byte_count > secondary.tesseract.stdout_max_bytes
                            or p.stderr_byte_count > secondary.tesseract.stderr_max_bytes for p in attempt.processes)):
                    raise InputError('saved Tesseract attempts differ from ordered frozen producer/process limits')
                if attempt.status == 'observed' and (tuple(p.phase for p in attempt.processes) != ('version','ocr')
                        or any(not p.started or p.exit_code != 0 or p.error_code is not None for p in attempt.processes)):
                    raise InputError('successful Tesseract quality observation lacks two successful actual process phases')
        starts += sum(_attempt_starts(a) for a in attempts)
        used += len(numbers)
        if saved is None:
            continue
        if saved.status == 'reviewed':
            if len(saved.attempts) != len(secondary.regions) or saved.review != _review_from_attempts(audit,secondary,saved.attempts):
                raise InputError('saved secondary review differs from deterministic selected OCR policy')
            if artifact is None or artifact.get('kind') == 'execution_error':
                raise InputError('completed secondary review lacks its resulting audit')
            final = parse_report(json.dumps(artifact))
            if apply_supplemental_review(audit,saved.primary_report,saved.review) != final:
                raise InputError('saved quality audit does not follow its actual selected evidence path')
        elif artifact is not None and artifact.get('kind') != 'execution_error':
            raise InputError('selected secondary quality path was bypassed by a primary report')
        if saved.status == 'not_run' and (saved.primary_report is not None or saved.attempts):
            raise InputError('not-run secondary branch cannot claim observations')
    return used,starts,unresolved


def assess_quality_run(bundle_dir: str | Path, run_dir: str | Path, *,
                       config_path: str | Path | None = None,
                       execution_binding: str | Path | None = None,
                       capability_path=None, language_profile_path=None,
                       secondary_policy_path=None) -> QualityReceiptV1 | QualityReceiptV2:
    """Read existing v2 run artifacts; naked live=True is never transport proof."""
    root = _safe_path(Path(bundle_dir), file=False)
    policies = dict(config_path=config_path,capability_path=capability_path,
                    language_profile_path=language_profile_path,secondary_policy_path=secondary_policy_path)
    manifest = verify_frozen_quality(root, **policies)
    v2 = isinstance(manifest,FrozenQualityManifestV2)
    out = _safe_path(Path(run_dir), file=False)
    if out.is_relative_to(root) or root.is_relative_to(out):
        raise InputError('quality results must be separate from the frozen bundle')
    with file_lock(out / '.run.lock'):
        run = read_run(out)
        _validate_run_spec(root, manifest, run)
        rows, calls = read_results(out, run)
        jobs = run['spec']['jobs']
        # Reports carry their own input/prompt binding; error slots remain errors.
        for i in rows:
            if not (out / f'{i:04d}.json').exists():
                # read_results retains interrupted pending calls as recovered errors.
                continue
            artifact = _decode_json(_read(_reference(out, f'{i:04d}.json')))
            if artifact.get('kind') != 'execution_error':
                from .adapters.local_input import load_input
                from .dispatch import _bound_report
                report = parse_report(json.dumps(artifact))
                audit = load_input(_reference(root,jobs[i]['input_path']))
                report = _bound_report(report,audit,model=run['spec']['provider'],live=run['spec']['live'])
                if (report.schema_version != 3 or report.input_fingerprint != jobs[i]['input_sha256']
                        or report.prompt_version != manifest.prompt_version
                        or report.execution_mode != ('live' if run['spec']['live'] else 'fake')):
                    raise InputError('quality report input/prompt/mode binding differs')
                if (report.decision == 'pass' and run['spec']['live']
                        and not any(c.live and c.status == 'ok' and c.transport_status == 'succeeded'
                                    and c.validation_status == 'valid' and c.http_status == 200 for c in report.calls)):
                    raise InputError('live quality pass report lacks a validated service response')
        local_used, local_starts, local_unresolved = _verify_v2_run_evidence(root,out,manifest,run,rows,
            _secondary_policy(secondary_policy_path)) if v2 else (0,0,0)
        artifacts = _run_artifacts(out, jobs,manifest=manifest)
        complete_count = sum((out / f'{i:04d}.result.json').is_file() for i in range(len(jobs)))
    evidence = 'fake_replay' if not run['spec']['live'] else 'unverified'
    reasons = []
    if execution_binding is not None:
        binding_path = _safe_path(Path(execution_binding))
        binding_raw = _read(binding_path)
        value = _parse(binding_raw, QualityExecutionV2 if v2 else QualityExecutionV1)
        start_raw = _read(_reference(binding_path.parent, 'quality_start.json'))
        start = _parse(start_raw, QualityStartV2 if v2 else QualityStartV1)
        if (value.bundle_seal_sha256 != manifest.seal_sha256 or value.run_fingerprint != run['fingerprint']
                or value.run_artifacts_sha256 != _artifacts_hash(artifacts)
                or value.actual_calls_sha256 != fingerprint([c.model_dump(mode='json') for c in calls])
                or value.start_sha256 != hashlib.sha256(start_raw).hexdigest()
                or start.bundle_seal_sha256 != manifest.seal_sha256 or start.plan != manifest.plan
                or start.evidence_kind != value.evidence_kind):
            raise InputError('quality execution provenance binding differs')
        if v2 and (value.policy_binding_sha256 != _quality_policy_hash(manifest)
                   or start.policy_binding_sha256 != _quality_policy_hash(manifest)):
            raise InputError('quality execution selected policy binding differs')
        evidence = value.evidence_kind
        if (evidence == 'fake_replay') != (not run['spec']['live']):
            raise InputError('quality execution mode differs from saved calls')
        artifacts.append(QualityArtifactV1(path='quality_execution.json', sha256=hashlib.sha256(binding_raw).hexdigest(),
                                         size_bytes=len(binding_raw), kind='observation'))
        artifacts.append(QualityArtifactV1(path='quality_start.json', sha256=hashlib.sha256(start_raw).hexdigest(),
                                         size_bytes=len(start_raw), kind='observation'))
    if evidence != 'live_service':
        reasons.append('real_service_provenance_missing')
    if manifest.source_purpose != 'human_labeled' or manifest.label_kind != 'independent_human':
        reasons.append('independent_human_labels_missing')
    if manifest.threshold_state != 'agreed':
        reasons.append('thresholds_not_agreed')
    if local_unresolved:
        reasons.append('local_tool_accounting_incomplete')
    complete = len(rows) == len(jobs) and complete_count == len(jobs)
    if not complete:
        reasons.append('planned_results_incomplete')
    if evidence == 'live_service':
        if not calls or not all(c.live for c in calls):
            reasons.append('actual_live_calls_missing')
        if any(c.validation_status == 'pending' for c in calls):
            reasons.append('call_validation_incomplete')
        if any(r['decision'] is not None and not r['attempts'] for r in rows.values()):
            reasons.append('report_live_call_missing')
    metrics = planned_quality_metrics(jobs, rows)
    per_repeat = {}
    for repeat in range(1, manifest.plan.repeats + 1):
        selected = [i for i, job in enumerate(jobs) if job['repeat'] == repeat]
        per_repeat[str(repeat)] = planned_quality_metrics([jobs[i] for i in selected],
            {position: rows[i] for position, i in enumerate(selected) if i in rows})
    threshold = _parse(_read(root / 'thresholds.json'), ThresholdProfileV1)
    gates = evaluate_quality_gates(threshold, metrics, per_repeat)
    acceptance = 'not_assessed' if reasons else ('passed' if all(g['passed'] for g in gates) else 'failed')
    usage = usage_summary(calls)
    usage['quality_is_not_usage_or_invoice'] = True
    usage['usage_completeness'] = ('no_service_calls' if not usage['live_attempts'] else
                                 'incomplete' if usage['attempts_with_unknown_usage'] else 'complete')
    usage['service_response_models'] = sorted({c.response_model for c in calls if c.live and c.response_model})
    usage['service_model_identity_missing_attempts'] = sum(c.live and not c.response_model for c in calls)
    if v2:
        usage['model_attempts'] = len(calls)
    body = dict(schema_version=2 if v2 else 1, kind='a4_quality_receipt', bundle_seal_sha256=manifest.seal_sha256,
        run_fingerprint=run['fingerprint'], evidence_kind=evidence, status='complete' if complete else 'incomplete',
        quality_acceptance=acceptance, reason_codes=reasons, planned_audits=len(jobs), recorded_audits=len(rows),
        completed_result_files=complete_count, missing_audits=len(jobs) - len(rows),
        execution_errors=sum(r['decision'] is None for r in rows.values()),
        unknown_decisions=sum(r['decision'] == 'unknown' for r in rows.values()),
        metrics=metrics, per_repeat=per_repeat, quality_gates=list(gates), usage=usage,
        artifacts=[a.model_dump(mode='json') for a in sorted(artifacts, key=lambda a: a.path)])
    if v2:
        body.update(validation_path=manifest.validation_path,execution_mode=manifest.execution_mode,
            experiment_identity_sha256=manifest.experiment_identity.identity_sha256,
            capability_sha256=manifest.capability_sha256,language_profile_sha256=manifest.language_profile_sha256,
            secondary_policy_sha256=manifest.secondary_policy_sha256,secondary_tool_sha256=manifest.secondary_tool_sha256,
            local_tool_attempts=local_used if not local_unresolved else None,
            local_tool_process_starts=local_starts if not local_unresolved else None,
            local_tool_known_attempts=local_used,local_tool_known_process_starts=local_starts,
            local_tool_unresolved_invocations=local_unresolved,claim_scope='selected_audit_path_only')
    if verify_frozen_quality(root, **policies) != manifest:
        raise InputError('quality bundle changed during assessment')
    return _parse(_json_bytes(dict(body, receipt_sha256=fingerprint(body))), QualityReceiptV2 if v2 else QualityReceiptV1)


def verify_quality_receipt(receipt_path: str | Path, bundle_dir: str | Path, run_dir: str | Path, *,
                           config_path: str | Path | None = None,
                           execution_binding: str | Path | None = None,
                           capability_path=None, language_profile_path=None,
                           secondary_policy_path=None) -> QualityReceiptV1 | QualityReceiptV2:
    """Recompute a saved receipt from its current sources instead of trusting its seal."""
    raw = _read(_safe_path(Path(receipt_path)))
    saved = _versioned_quality(raw,QualityReceiptV1,QualityReceiptV2)
    current = assess_quality_run(bundle_dir, run_dir, config_path=config_path,
        execution_binding=execution_binding,capability_path=capability_path,
        language_profile_path=language_profile_path,secondary_policy_path=secondary_policy_path)
    if current != saved:
        raise InputError('saved quality receipt differs from its current evidence')
    return current


def _validated_benchmark_cases(cases,jobs):
    from .telemetry import BenchmarkCaseV1
    if cases is None:
        return {}
    if not isinstance(cases,Mapping):
        raise InputError('quality_benchmark_cases_require_explicit_mapping')
    result = {}
    for index,case in cases.items():
        if type(index) is not int or not 0 <= index < len(jobs) or not isinstance(case,BenchmarkCaseV1):
            raise InputError('quality_benchmark_case_index_or_contract_invalid')
        case = BenchmarkCaseV1.model_validate_json(case.model_dump_json())
        job = jobs[index]
        if case.sample_id != job['sample_id'] or case.repetition != job['repeat'] or case.warmup:
            raise InputError('quality_benchmark_case_does_not_match_frozen_job')
        result[index] = case
    if len({c.case_id for c in result.values()}) != len(result) or len({(c.spec_sha256,c.arm) for c in result.values()}) > 1:
        raise InputError('quality_benchmark_cases_require_unique_cases_for_one_frozen_arm')
    return result


def _saved_benchmark_cases(spec,jobs):
    from .telemetry import BenchmarkCaseV1
    value = spec.get('benchmark_cases')
    if value is None:
        return {}
    if not isinstance(value,dict) or any(not isinstance(k,str) or not k.isdecimal() or str(int(k)) != k for k in value):
        raise InputError('saved_quality_benchmark_case_mapping_invalid')
    return _validated_benchmark_cases({int(k):_parse(_json_bytes(v),BenchmarkCaseV1) for k,v in value.items()},jobs)


def _verify_case_telemetry(out,index,job,manifest,case_policy,artifact,expected_case,spec):
    from .evaluation_io import artifact_calls
    from .telemetry import TelemetryEnvelopeV1
    path = out/f'{index:04d}.telemetry.json'
    if expected_case is None:
        if path.exists():
            raise InputError('unselected_quality_job_cannot_claim_benchmark_telemetry')
        return
    if not path.exists():
        if (out/f'{index:04d}.result.json').exists():
            raise InputError('completed_selected_benchmark_case_lacks_its_actual_telemetry')
        return
    measured = _parse(_read(path),TelemetryEnvelopeV1)
    calls = artifact_calls(artifact,job,spec) if artifact else ()
    decision = artifact.get('decision') if artifact and artifact.get('kind') != 'execution_error' else None
    if (measured.benchmark_case != expected_case or measured.sample_id != job['sample_id']
            or measured.input_fingerprint != job['input_sha256'] or measured.image_sha256 != job['image_sha256']
            or measured.execution_mode != manifest.execution_mode or measured.scope != 'audit_only'
            or measured.delivery_state != 'not_called' or measured.calls != calls or measured.audit_decision != decision
            or measured.measurement_status != 'recorded'):
        raise InputError('quality_case_telemetry_does_not_match_its_actual_audit_invocation')
    if (decision is not None) != (measured.terminal_outcome == 'completed'):
        raise InputError('quality_case_telemetry_terminal_state_differs_from_its_audit')
    result_path = out/f'{index:04d}.result.json'
    if result_path.exists() and _decode_json(_read(result_path)).get('elapsed_ms') != round(measured.elapsed_total_ms,3):
        raise InputError('quality_result_latency_differs_from_its_same_invocation_telemetry')
    if measured.experiment_identity is not None:
        identity = ExperimentIdentityV1.model_validate_json(json.dumps(measured.experiment_identity))
        fields = manifest.experiment_identity.model_dump(mode='json',exclude={'identity_sha256'})
        fields['execution_policy_sha256'] = case_policy.effective_execution_policy_sha256
        expected = ExperimentIdentityV1.model_validate_json(json.dumps(dict(fields,identity_sha256=fingerprint(fields))))
        if identity != expected:
            raise InputError('quality_case_telemetry_source_or_effective_execution_identity_differs')
    elif decision is not None:
        raise InputError('completed_quality_benchmark_report_lacks_actual_source_identity')
    sidecar_path = out/f'{index:04d}.evidence.json'
    if sidecar_path.exists():
        from .telemetry import ToolSummaryV1
        saved = _parse(_read(sidecar_path),QualitySecondaryRecordV1)
        attempts = saved.attempts
        expected_tools = ToolSummaryV1(logical_requests=len(attempts),
            admitted_attempts=sum(_attempt_number(a)>0 for a in attempts),process_starts=sum(_attempt_starts(a) for a in attempts),
            denied_requests=sum(a.status == 'budget_refused' for a in attempts),
            failed_requests=sum(a.status in ('failed','unavailable') for a in attempts),
            cancelled_requests=sum(a.status == 'cancelled' for a in attempts))
        if (measured.tool_summary is not None or attempts) and measured.tool_summary != expected_tools:
            raise InputError('quality_case_telemetry_tool_accounting_differs_from_its_actual_evidence')
    elif manifest.validation_path == 'primary_only' and measured.tool_summary is not None:
        raise InputError('primary_only_quality_case_cannot_claim_tool_telemetry')


async def _run_quality_v2_dataset(root, out, manifest, provider, language, secondary,*,benchmark_cases=None):
    """Owned audit-only runner. Selected profiles/tool branch cannot be bypassed."""
    from time import perf_counter
    from .adapters.local_input import load_input
    from .engine import AuditEngine
    from .errors import BudgetExceededError
    from .evaluate import _summary
    from .evaluation_io import save_result,export_rows
    from .identity import input_fingerprint
    from .providers.fake import FakeProvider
    from .runtime import StandaloneExecutor
    from .telemetry import execution_policy_fingerprint,TelemetryRecorder,provider_measurement_kind
    dataset = _parse(_read(root/'dataset.json'),Dataset)
    plan, prepared, input_hashes = manifest.plan, [], {}
    capability = load_capability(root/'capability.json') if manifest.capability_sha256 else None
    config = _config(root/'config.json') if manifest.provider_config_sha256 else None
    prompt = files('poster_critic').joinpath('prompts/critic_v4.txt').read_text(encoding='utf-8')
    tool = parse_frozen_quality_tool(_read(root/'tool_identity.json')) if secondary else None
    client_policy = _manifest_client_policy(manifest)
    for sample in dataset.samples:
        audit = load_input(_reference(root,sample.input_path))
        input_hashes[sample.sample_id] = input_fingerprint(audit)
        if sample.split != 'validation':
            continue
        replay,response_hash = None,None
        if provider is None:
            if sample.response_path is None:
                raise InputError('offline quality replay requires fixed observations')
            replay = FakeProvider.from_file(_reference(root,sample.response_path))
            replay.endpoint_family,replay.region = manifest.endpoint_family,manifest.region
            replay.config,replay.capability = config,capability
            response_hash = hashlib.sha256(_read(_reference(root,sample.response_path))).hexdigest()
        if secondary:
            # Validate all selected ROIs before output and before any model call.
            backend = TesseractOCR(secondary.tesseract) if secondary.backend == 'tesseract' else VisionOCR(secondary.apple_vision)
            EvidenceReviewer(ocr=backend,regions=secondary.regions,
                max_tool_attempts=secondary.max_tool_attempts,
                tool_timeout_seconds=secondary.tool_timeout_seconds).preflight(audit)
        prepared.append((sample,audit,replay,response_hash))
    live = manifest.execution_mode == 'live'
    evidence = 'live_model_evaluation' if live and dataset.purpose == 'human_labeled' else 'software_demo_only'
    jobs = [dict(sample.model_dump(mode='json'),repeat=repeat,image_sha256=audit.image.sha256,
        input_sha256=input_hashes[sample.sample_id],response_sha256=response_hash)
        for repeat in range(1,plan.repeats+1) for sample,audit,_,response_hash in prepared]
    selected_cases = _validated_benchmark_cases(benchmark_cases,jobs)
    spec = dict(purpose=dataset.purpose,live=live,evidence_kind=evidence,
        dataset_sha256=hashlib.sha256(_read(root/'dataset.json')).hexdigest(),input_hashes=input_hashes,
        split='validation',repeats=plan.repeats,jobs=jobs,software_sha256=manifest.software_fingerprint_v1,
        prompt_version=manifest.prompt_version,provider=provider.name if provider else 'fake-fixed-v1',
        max_total_attempts=plan.max_total_attempts,max_attempts_per_audit=plan.max_attempts_per_audit,
        request_timeout=plan.request_timeout,total_timeout=plan.total_timeout,
        provider_config=config.model_dump(mode='json') if live else None,
        flat_price_per_million={'input':None,'output':None,'currency':None},quality_protocol_version=2,
        policy_binding_sha256=_quality_policy_hash(manifest),experiment_identity_sha256=manifest.experiment_identity.identity_sha256,
        validation_path=manifest.validation_path,execution_mode=manifest.execution_mode,claim_scope='selected_audit_path_only')
    if client_policy != 'per_request':
        spec['client_policy'] = client_policy
    if benchmark_cases is not None:
        spec['benchmark_cases'] = {str(i):case.model_dump(mode='json') for i,case in sorted(selected_cases.items())}
    out.mkdir(parents=True,exist_ok=False)
    run = dict(schema_version=2,fingerprint=fingerprint(spec),spec=spec,evidence_kind=evidence,planned_audits=len(jobs))
    with file_lock(out/'.run.lock'):
        write_json(out/'run.json',run)
        rows,all_calls = {},[]
        for index,job in enumerate(jobs):
            sample,audit,replay,_ = prepared[index % len(prepared)]
            started = perf_counter()
            report,error,executor,interrupted = None,None,None,False
            remaining = max(0,plan.max_total_attempts-len(all_calls))
            limit = min(plan.max_attempts_per_audit,remaining) if remaining else None
            case = QualityCasePolicyV1(policy_binding_sha256=_quality_policy_hash(manifest),
                planned_execution_policy_sha256=manifest.experiment_identity.execution_policy_sha256,
                effective_execution_policy_sha256=_planned_execution_hash(plan,manifest.endpoint_family,manifest.region,
                    limit=limit,client_policy=client_policy) if limit else None,
                sample_id=audit.sample_id,input_fingerprint=input_fingerprint(audit),index=index,
                remaining_model_attempts=remaining,effective_max_attempts=limit)
            write_json(out/f'{index:04d}.policy.json',case.model_dump(mode='json'))
            pending = out/f'{index:04d}.pending.json'
            def journal(started_attempts,calls):
                write_json(pending,dict(run_fingerprint=run['fingerprint'],index=index,started_attempts=started_attempts,
                    calls=[c.model_dump(mode='json') for c in calls],elapsed_ms=round((perf_counter()-started)*1000,3)),replace=True)
            journal(0,())
            def persist_tool(state,current,budget,logical_invocations):
                saved = QualityToolJournalV1(sample_id=current.sample_id,input_fingerprint=input_fingerprint(current),
                    secondary_policy_sha256=manifest.secondary_policy_sha256,tool_sha256=tool.tool_sha256,
                    logical_invocations=logical_invocations,state=state,attempts=tuple(budget.attempts))
                write_json(out/f'{index:04d}.tool.pending.json',saved.model_dump(mode='json'),replace=True)
            backend_class = QualityTesseractOCR if secondary and secondary.backend == 'tesseract' else QualityVisionOCR
            reviewer = QualityEvidenceReviewer(ocr=backend_class(
                secondary.tesseract if secondary.backend == 'tesseract' else secondary.apple_vision,journal_writer=persist_tool),regions=secondary.regions,
                max_tool_attempts=secondary.max_tool_attempts,tool_timeout_seconds=secondary.tool_timeout_seconds) if secondary else None
            if reviewer:
                reviewer.frozen_identity_policy = secondary
            selected_provider = provider if provider else replay
            telemetry = TelemetryRecorder(execution_mode=manifest.execution_mode,measurement_kind=provider_measurement_kind(selected_provider),
                scope='audit_only',benchmark_case=selected_cases[index]) if index in selected_cases else None
            if telemetry:
                telemetry.bind_input(audit)
            try:
                if limit is None:
                    raise BudgetExceededError('quality cumulative model attempt budget exhausted')
                # Controlled replay can exercise request admissibility without a paid request.
                if not live and capability:
                    prepare_request(audit,prompt,config,capability,endpoint_family=manifest.endpoint_family,region=manifest.region)
                executor = StandaloneExecutor(limit,allow_live=live,request_timeout=plan.request_timeout,
                    total_timeout=plan.total_timeout,on_update=journal)
                if execution_policy_fingerprint(selected_provider,executor,max_retries=limit-1) != case.effective_execution_policy_sha256:
                    raise InputError('actual quality execution policy differs before request admission')
                report = await AuditEngine(selected_provider,executor,max_retries=limit-1,
                    evidence_reviewer=reviewer,language_profile=language,telemetry=telemetry).audit(audit)
            except asyncio.CancelledError:
                error,interrupted = 'CancelledError',True
            except KeyboardInterrupt:
                error,interrupted = 'KeyboardInterrupt',True
            except Exception as exc:
                # Preserve every admitted model/tool attempt; no error slot is retried.
                error = type(exc).__name__
            calls = executor.records if executor else ()
            if reviewer:
                attempts = tuple(reviewer.last_tool_budget.attempts) if reviewer.last_tool_budget else ()
                primary = reviewer.primary_report
                status = 'reviewed' if report else 'cancelled' if interrupted else 'not_run' if primary is None and not attempts else 'failed'
                saved = QualitySecondaryRecordV1(sample_id=audit.sample_id,input_fingerprint=input_fingerprint(audit),
                    secondary_policy_sha256=manifest.secondary_policy_sha256,tool_sha256=tool.tool_sha256,
                    status=status,primary_report=primary,review=reviewer.last_review if report else None,attempts=attempts)
                write_json(out/f'{index:04d}.evidence.json',saved.model_dump(mode='json'))
            value = report.model_dump(mode='json') if report else dict(kind='execution_error',error_type=error,
                sample_id=sample.sample_id,image_sha256=audit.image.sha256,calls=[c.model_dump(mode='json') for c in calls])
            if telemetry:
                if telemetry._ended is None:
                    telemetry.bind_calls(lambda:calls)
                    telemetry.finish(terminal_outcome='cancelled' if interrupted else 'input_error' if error == 'InputError' else 'execution_error',
                                     delivery_state='not_called')
                elapsed = telemetry.snapshot().elapsed_total_ms
            else:
                elapsed = (perf_counter()-started)*1000
            rows[index] = save_result(out,index,value,calls,job,elapsed)
            if telemetry:
                telemetry.write(out/f'{index:04d}.telemetry.json')
            all_calls.extend(calls)
            export_rows(out,rows)
            if interrupted:
                if error == 'KeyboardInterrupt':
                    raise KeyboardInterrupt
                raise asyncio.CancelledError
        write_json(out/'summary.json',_summary(run,rows,tuple(all_calls),complete=True))


async def _run_frozen_quality_v2(root,output_dir,manifest,*,allow_live,config_path,
                                 capability_path,language_profile_path,secondary_policy_path,benchmark_cases=None):
    policies = dict(config_path=config_path,capability_path=capability_path,
        language_profile_path=language_profile_path,secondary_policy_path=secondary_policy_path)
    out = _new_destination(output_dir)
    if out.is_relative_to(root) or root.is_relative_to(out):
        raise InputError('quality output must be separate from the frozen bundle')
    provider = None
    dataset = _parse(_read(root/'dataset.json'),Dataset)
    _validated_benchmark_cases(benchmark_cases,[dict(sample.model_dump(mode='json'),repeat=repeat)
        for repeat in range(1,manifest.plan.repeats+1) for sample in dataset.samples if sample.split == 'validation'])
    if allow_live:
        from .providers.chat import ChatProvider
        from .telemetry import provider_measurement_kind
        provider = ChatProvider(_config(config_path),capability=load_capability(root/'capability.json'),
                                endpoint_family=manifest.endpoint_family,region=manifest.region)
        if provider_measurement_kind(provider) != 'live_service':
            raise InputError('quality_requires_actual_live_service_transport; mock/custom provider not admitted')
    language = read_language_profile(root/'language.json')
    secondary = _secondary_policy(secondary_policy_path)
    # Replay sources and all current producer/profile bytes are checked before creation.
    if not allow_live and any(s.response_path is None for s in dataset.samples if s.split == 'validation'):
        raise InputError('offline quality replay requires fixed observations')
    out.mkdir(parents=True)
    start = QualityStartV2(bundle_seal_sha256=manifest.seal_sha256,
        evidence_kind='live_service' if allow_live else 'fake_replay',plan=manifest.plan,
        policy_binding_sha256=_quality_policy_hash(manifest))
    write_json(out/'quality_start.json',start.model_dump(mode='json'))
    if _manifest_client_policy(manifest) == 'scoped_owned':
        from .providers.chat import ProviderSession
        async with ProviderSession(provider):
            await _run_quality_v2_dataset(root,out/'run',manifest,provider,language,secondary,benchmark_cases=benchmark_cases)
    else:
        await _run_quality_v2_dataset(root,out/'run',manifest,provider,language,secondary,benchmark_cases=benchmark_cases)
    if verify_frozen_quality(root,**policies) != manifest:
        raise InputError('quality source/plan changed during execution; retain artifacts, reject completion')
    run = read_run(out/'run')
    with file_lock(out/'run/.run.lock'):
        _,calls = read_results(out/'run',run)
        artifacts = _run_artifacts(out/'run',run['spec']['jobs'],manifest=manifest)
    body = dict(schema_version=2,kind='a4_quality_execution',bundle_seal_sha256=manifest.seal_sha256,
        run_fingerprint=run['fingerprint'],evidence_kind=start.evidence_kind,call_schema_version=2,
        policy_binding_sha256=_quality_policy_hash(manifest),start_sha256=hashlib.sha256(_read(out/'quality_start.json')).hexdigest(),
        actual_calls_sha256=fingerprint([c.model_dump(mode='json') for c in calls]),run_artifacts_sha256=_artifacts_hash(artifacts))
    write_json(out/'quality_execution.json',dict(body,execution_sha256=fingerprint(body)))
    receipt = assess_quality_run(root,out/'run',execution_binding=out/'quality_execution.json',**policies)
    write_json(out/'quality_receipt.json',receipt.model_dump(mode='json'))
    return receipt


async def run_frozen_quality(bundle_dir: str | Path, output_dir: str | Path, *,
                             config_path: str | Path | None = None,
                             allow_live: bool = False,capability_path=None,language_profile_path=None,
                             secondary_policy_path=None,benchmark_cases=None) -> QualityReceiptV1 | QualityReceiptV2:
    """Run a fixed plan once. Default is Fake replay; no dispatch or silent retry."""
    if type(allow_live) is not bool:
        raise InputError('allow_live must be an explicit boolean')
    root = _safe_path(Path(bundle_dir), file=False)
    policies = dict(config_path=config_path,capability_path=capability_path,
                    language_profile_path=language_profile_path,secondary_policy_path=secondary_policy_path)
    manifest = verify_frozen_quality(root, **policies)
    if isinstance(manifest,FrozenQualityManifestV2):
        if allow_live != (manifest.execution_mode == 'live'):
            raise InputError('quality_run_mode_differs_from_frozen_selection')
        return await _run_frozen_quality_v2(root,output_dir,manifest,allow_live=allow_live,benchmark_cases=benchmark_cases,**policies)
    if benchmark_cases is not None:
        raise InputError('legacy_quality_cannot_claim_benchmark_cases')
    out = _new_destination(output_dir)
    if out.is_relative_to(root) or root.is_relative_to(out):
        raise InputError('quality output must be separate from the frozen bundle')
    provider = None
    if allow_live:
        if config_path is None:
            raise InputError('live quality validation requires explicit matching config')
        from .providers.chat import ChatProvider
        from .telemetry import provider_measurement_kind
        # No transport/provider injection: Mock live records are not real evidence.
        provider = ChatProvider(_config(config_path))
        if provider_measurement_kind(provider) != 'live_service':
            raise InputError('quality_requires_actual_live_service_transport; mock/custom provider not admitted')
    else:
        dataset = _parse(_read(root / 'dataset.json'), Dataset)
        if any(s.response_path is None for s in dataset.samples if s.split == 'validation'):
            raise InputError('offline quality replay requires fixed observations; use explicit allow_live for service calls')
    out.mkdir(parents=True)
    write_json(out / 'quality_start.json', {'schema_version': 1, 'kind': 'a4_quality_start',
        'bundle_seal_sha256': manifest.seal_sha256, 'evidence_kind': 'live_service' if allow_live else 'fake_replay',
        'plan': manifest.plan.model_dump(mode='json'), 'call_schema_version': 2})
    plan = manifest.plan
    await run_dataset(root / 'dataset.json', out / 'run', provider=provider, split='validation',
        repeats=plan.repeats, max_attempts=plan.max_attempts_per_audit,
        max_total_attempts=plan.max_total_attempts, request_timeout=plan.request_timeout,
        total_timeout=plan.total_timeout)
    if verify_frozen_quality(root, config_path=config_path) != manifest:
        raise InputError('quality source/plan changed during execution; retain artifacts, reject completion')
    run = read_run(out / 'run')
    with file_lock(out / 'run/.run.lock'):
        _, calls = read_results(out / 'run', run)
        artifacts = _run_artifacts(out / 'run', run['spec']['jobs'])
    body = {'schema_version': 1, 'kind': 'a4_quality_execution', 'bundle_seal_sha256': manifest.seal_sha256,
            'run_fingerprint': run['fingerprint'], 'evidence_kind': 'live_service' if allow_live else 'fake_replay',
            'call_schema_version': 2, 'start_sha256': hashlib.sha256(_read(out / 'quality_start.json')).hexdigest(),
            'actual_calls_sha256': fingerprint([c.model_dump(mode='json') for c in calls]),
            'run_artifacts_sha256': _artifacts_hash(artifacts)}
    write_json(out / 'quality_execution.json', dict(body, execution_sha256=fingerprint(body)))
    receipt = assess_quality_run(root, out / 'run', config_path=config_path,
                                 execution_binding=out / 'quality_execution.json')
    write_json(out / 'quality_receipt.json', receipt.model_dump(mode='json'))
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    freeze = sub.add_parser('freeze')
    freeze.add_argument('--dataset', type=Path, required=True)
    freeze.add_argument('--out', type=Path, required=True)
    freeze.add_argument('--label-source', type=Path, required=True)
    freeze.add_argument('--thresholds', type=Path, required=True)
    freeze.add_argument('--agreement-source', type=Path)
    freeze.add_argument('--label-kind', choices=['independent_human', 'model_proxy', 'software_control', 'unverified'], default='unverified')
    freeze.add_argument('--label-reference', default='provided_label_sheet')
    freeze.add_argument('--repeats', type=int, choices=[1, 2], default=2)
    freeze.add_argument('--attempts', type=int, choices=[1, 2], default=2)
    freeze.add_argument('--max-total-attempts', type=int, default=48)
    freeze.add_argument('--request-timeout', type=float, default=30.0)
    freeze.add_argument('--total-timeout', type=float, default=90.0)
    freeze.add_argument('--protocol-version',type=int,choices=[1,2],default=1)
    freeze.add_argument('--validation-path',choices=['primary_only','primary_plus_local_ocr'],default='primary_only')
    freeze.add_argument('--execution-mode',choices=['fake','live'],default='fake')
    freeze.add_argument('--endpoint-family',default='chat_completions')
    freeze.add_argument('--region')
    freeze.add_argument('--client-policy',choices=['per_request','scoped_owned'],default='per_request')
    for name in ('verify', 'run', 'assess'):
        command = sub.add_parser(name)
        command.add_argument('--bundle', type=Path, required=True)
        if name in ('run', 'assess'):
            command.add_argument('--out', type=Path, required=True)
        if name == 'run':
            command.add_argument('--allow-live', action='store_true')
        if name == 'assess':
            command.add_argument('--run-dir', type=Path, required=True)
            command.add_argument('--execution-binding', type=Path)
    for command in sub.choices.values():
        command.add_argument('--config', type=Path)
        command.add_argument('--capability',type=Path)
        command.add_argument('--language-profile',type=Path)
        command.add_argument('--secondary-review',type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'freeze':
            if args.protocol_version == 1 and (args.capability is not None or args.language_profile is not None
                    or args.secondary_review is not None or args.validation_path != 'primary_only'
                    or args.execution_mode != 'fake' or args.endpoint_family != 'chat_completions' or args.region is not None
                    or args.client_policy != 'per_request'):
                raise InputError('new policy claims require --protocol-version 2')
            p2 = dict(capability_path=args.capability,language_profile_path=args.language_profile,
                secondary_policy_path=args.secondary_review,validation_path=args.validation_path,
                execution_mode=args.execution_mode,endpoint_family=args.endpoint_family,region=args.region,
                client_policy=args.client_policy) if args.protocol_version == 2 else {}
            freezer = freeze_quality_validation_v2 if args.protocol_version == 2 else freeze_quality_validation
            result = freezer(args.dataset, args.out, label_source=args.label_source,
                threshold_path=args.thresholds, label_kind=args.label_kind, label_reference=args.label_reference,
                agreement_source=args.agreement_source, config_path=args.config, repeats=args.repeats,
                max_attempts=args.attempts, max_total_attempts=args.max_total_attempts,
                request_timeout=args.request_timeout, total_timeout=args.total_timeout,**p2)
        elif args.command == 'verify':
            result = verify_frozen_quality(args.bundle, config_path=args.config,capability_path=args.capability,
                language_profile_path=args.language_profile,secondary_policy_path=args.secondary_review)
        elif args.command == 'run':
            result = asyncio.run(run_frozen_quality(args.bundle, args.out,
                config_path=args.config, allow_live=args.allow_live,capability_path=args.capability,
                language_profile_path=args.language_profile,secondary_policy_path=args.secondary_review))
        else:
            _new_destination(args.out)
            result = assess_quality_run(args.bundle, args.run_dir, config_path=args.config,
                execution_binding=args.execution_binding,capability_path=args.capability,
                language_profile_path=args.language_profile,secondary_policy_path=args.secondary_review)
            write_json(args.out, result.model_dump(mode='json'))
        print(result.model_dump_json(indent=2))
        return 0
    except (InputError, ProviderError, ValueError, OSError):
        print('quality validation error: source, plan, agreement, saved evidence or publication invalid; existing artifacts preserved')
        return 2
    except KeyboardInterrupt:
        print('quality validation interrupted; keep saved calls, do not repeat automatically')
        return 130


if __name__ == '__main__':
    raise SystemExit(main())

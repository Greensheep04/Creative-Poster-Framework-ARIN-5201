"""Explicit existing Tesseract producer freeze, with no installation or fallback.

The version probe is local freeze evidence. Binary hashes describe the caller's
selected trusted tool, not an authenticated vendor build or recognition quality.
Read-only verification checks current bytes; each audit rechecks the actual
version and producer through TesseractOCR's bounded version/OCR processes.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import time
import tempfile
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .contracts import Contract, Text
from .errors import InputError
from .evidence import Digest
from .evidence_review import OCRRegionV1, SecondaryReviewConfigV1
from .experiment_identity import supplemental_policy_fingerprint
from .identity import fingerprint
from .ocr import (LanguageDataV1, OCRProducerV1, OCRProcessV1, TesseractOCR,
                  _MAX_BINARY_BYTES, _MAX_DATA_BYTES, _VERSION, _read_regular, _OCRFailure,_write_private_snapshot)


class FrozenTesseractPolicyV1(Contract):
    """Portable chosen settings. Private locators are retained only in hashes."""
    backend: Literal['tesseract'] = 'tesseract'
    languages: tuple[Text,...] = Field(min_length=1,max_length=4)
    psm: int = Field(ge=3,le=13)
    oem: int = Field(ge=0,le=3)
    timeout_seconds: float = Field(gt=0,le=300)
    stdout_max_bytes: int = Field(ge=1024,le=2*1024*1024)
    stderr_max_bytes: int = Field(ge=1024,le=256*1024)
    max_words: int = Field(ge=1,le=10000)
    regions: tuple[OCRRegionV1,...] = Field(min_length=1,max_length=8)
    max_tool_attempts: int = Field(ge=1,le=8)
    tool_timeout_seconds: float = Field(gt=0,le=300)

    @model_validator(mode='after')
    def distinct_regions(self):
        if len({r.copy_id for r in self.regions}) != len(self.regions):
            raise ValueError('frozen Tesseract copy regions must be distinct')
        return self


class FrozenTesseractToolV1(Contract):
    schema_version: Literal[1] = 1
    kind: Literal['a4_frozen_tesseract_tool'] = 'a4_frozen_tesseract_tool'
    producer: OCRProducerV1
    config_sha256: Digest
    secondary_policy_sha256: Digest
    safe_policy: FrozenTesseractPolicyV1
    version_process: OCRProcessV1
    version_response_sha256: Digest
    tool_sha256: Digest

    @field_validator('schema_version',mode='before')
    @classmethod
    def integer_version(cls,value):
        if type(value) is not int:
            raise ValueError('Tesseract quality version must be an integer')
        return value

    @model_validator(mode='after')
    def bound_identity(self):
        if (self.config_sha256 != self.producer.config_sha256
                or self.producer.psm != self.safe_policy.psm or self.producer.oem != self.safe_policy.oem
                or tuple(x.language for x in self.producer.language_data) != self.safe_policy.languages
                or self.safe_policy.psm == 12):
            raise ValueError('frozen Tesseract configuration and ordered producer differ')
        probe = self.version_process
        if (probe.phase != 'version' or not probe.started or probe.exit_code != 0
                or probe.error_code is not None or probe.stdout_byte_count < 1
                or probe.stdout_byte_count > self.safe_policy.stdout_max_bytes
                or probe.stderr_byte_count > self.safe_policy.stderr_max_bytes):
            raise ValueError('Tesseract freeze requires a successful bounded actual version probe')
        # Elapsed probe time is retained in the frozen artifact, not stable identity.
        if self.tool_sha256 != fingerprint(self.model_dump(mode='json',exclude={'tool_sha256','version_process'})):
            raise ValueError('frozen Tesseract content identity differs')
        return self


def _safe_policy(policy: SecondaryReviewConfigV1) -> FrozenTesseractPolicyV1:
    if not isinstance(policy,SecondaryReviewConfigV1) or policy.backend != 'tesseract':
        raise InputError('quality_tesseract_requires_explicit_selected_policy')
    policy = SecondaryReviewConfigV1.model_validate_json(policy.model_dump_json())
    fields = policy.tesseract.model_dump(mode='python',exclude={'schema_version','executable','tessdata_dir'})
    return FrozenTesseractPolicyV1(**fields,regions=policy.regions,max_tool_attempts=policy.max_tool_attempts,
                                  tool_timeout_seconds=policy.tool_timeout_seconds)


def _producer_bytes(policy,deadline=None):
    from .frozen_evaluation import _safe_path
    config = policy.tesseract
    executable = _safe_path(Path(config.executable))
    root = _safe_path(Path(config.tessdata_dir),file=False)
    binary = _read_regular(executable,_MAX_BINARY_BYTES,'tool_unavailable',deadline)
    data = tuple(LanguageDataV1(language=language,sha256=hashlib.sha256(raw).hexdigest(),byte_count=len(raw))
        for language in config.languages
        for raw in (_read_regular(_safe_path(root/f'{language}.traineddata'),_MAX_DATA_BYTES,
                                  'language_data_invalid',deadline),))
    return binary,data


def validate_frozen_tesseract_tool(policy: SecondaryReviewConfigV1,
                                   frozen: FrozenTesseractToolV1) -> FrozenTesseractToolV1:
    """No process execution. Bind typed frozen producer to current chosen bytes."""
    try:
        if not isinstance(frozen,FrozenTesseractToolV1):
            raise ValueError
        checked = FrozenTesseractToolV1.model_validate_json(frozen.model_dump_json())
        safe = _safe_policy(policy)
        binary,data = _producer_bytes(policy)
        actual = TesseractOCR(policy.tesseract)
        if (checked.config_sha256 != actual.config_sha256 or checked.safe_policy != safe
                or checked.secondary_policy_sha256 != supplemental_policy_fingerprint(policy)
                or checked.producer.executable_sha256 != hashlib.sha256(binary).hexdigest() or checked.producer.language_data != data):
            raise ValueError
        return checked
    except (ValueError,TypeError,OSError,_OCRFailure):
        raise InputError('current_quality_tesseract_tool_or_policy_differs') from None


async def freeze_tesseract_tool(policy: SecondaryReviewConfigV1) -> FrozenTesseractToolV1:
    """Run only the explicitly selected existing tool's bounded --version probe."""
    try:
        safe = _safe_policy(policy)
        config = policy.tesseract
        deadline = time.monotonic()+min(config.timeout_seconds,policy.tool_timeout_seconds)
        binary,data = _producer_bytes(policy,deadline)
        tool = TesseractOCR(config)
        traces = []
        with tempfile.TemporaryDirectory(prefix='a4-quality-tess-') as temporary:
            snapshot = Path(temporary).resolve(strict=True)/'a4-tesseract-snapshot'
            _write_private_snapshot(snapshot,binary,mode=0o700)
            raw = await tool._process((str(snapshot),'--version'),'version',deadline,traces)
            if _read_regular(snapshot,_MAX_BINARY_BYTES,'tool_identity_changed',deadline) != binary:
                raise ValueError
        match = _VERSION.fullmatch(raw.decode('utf-8').splitlines()[0])
        if match is None or len(traces) != 1 or time.monotonic() >= deadline:
            raise ValueError
        if _producer_bytes(policy,deadline) != (binary,data):
            raise ValueError
        producer = OCRProducerV1(engine_version=match[1],executable_sha256=hashlib.sha256(binary).hexdigest(),language_data=data,
            config_sha256=tool.config_sha256,psm=config.psm,oem=config.oem)
        fields = dict(schema_version=1,kind='a4_frozen_tesseract_tool',producer=producer.model_dump(mode='json'),
            config_sha256=tool.config_sha256,secondary_policy_sha256=supplemental_policy_fingerprint(policy),
            safe_policy=safe.model_dump(mode='json'),version_response_sha256=hashlib.sha256(raw).hexdigest())
        value = FrozenTesseractToolV1.model_validate_json(json.dumps(dict(fields,
            version_process=traces[0].model_dump(mode='json'),tool_sha256=fingerprint(fields))))
        return validate_frozen_tesseract_tool(policy,value)
    except asyncio.CancelledError:
        raise
    except (ValueError,TypeError,IndexError,UnicodeError,OSError,_OCRFailure):
        raise InputError('quality_tesseract_version_probe_failed') from None

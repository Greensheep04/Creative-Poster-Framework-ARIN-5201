"""Optional, bounded Tesseract observations bound to current poster pixels.

OCR words are auxiliary evidence. Confidence never becomes a QC pass. The
adapter snapshots the explicitly selected executable bytes and declared language
data. Both processes execute the same private byte snapshot. Tools relying on
their original location fail closed if the snapshot cannot execute them. It does
not install tools, infer text, retry, or execute a shell.

CLI/TSV reference: https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import tempfile
import time
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from .contracts import AuditInputV1, BBox, Contract, Text
from .errors import InputError
from .evidence import CropEvidenceV1, Digest, validate_crop_evidence
from .identity import input_fingerprint

_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
_LANGUAGE = re.compile(r"^(?:script/)?[A-Za-z][A-Za-z0-9_]*$")
_VERSION = re.compile(r"^tesseract ([0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[-+._A-Za-z0-9]*)?)$")
_MAX_DATA_BYTES = 128 * 1024 * 1024
_MAX_BINARY_BYTES = 128 * 1024 * 1024
_POLICY = "tesseract-crop-tsv-v1"


class TesseractConfigV1(Contract):
    schema_version: Literal[1] = 1
    executable: Text
    tessdata_dir: Text
    languages: Annotated[tuple[Text, ...], Field(min_length=1, max_length=4)] = ("eng",)
    psm: Annotated[int, Field(ge=3, le=13)] = 6
    oem: Annotated[int, Field(ge=0, le=3)] = 1
    timeout_seconds: Annotated[float, Field(gt=0, le=300)] = 10.0
    stdout_max_bytes: Annotated[int, Field(ge=1024, le=2 * 1024 * 1024)] = 1024 * 1024
    stderr_max_bytes: Annotated[int, Field(ge=1024, le=256 * 1024)] = 64 * 1024
    max_words: Annotated[int, Field(ge=1, le=10000)] = 5000

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("OCR schema version must be an integer")
        return value

    @model_validator(mode="after")
    def check_selection(self) -> Self:
        if not Path(self.executable).is_absolute() or not Path(self.tessdata_dir).is_absolute():
            raise ValueError("OCR executable and language data paths must be absolute")
        if any("\x00" in value for value in (self.executable, self.tessdata_dir)):
            raise ValueError("OCR paths must not contain NUL")
        if len(set(self.languages)) != len(self.languages) or any(not _LANGUAGE.fullmatch(x) for x in self.languages):
            raise ValueError("OCR languages must be unique explicit safe language IDs")
        if self.psm == 12:
            raise ValueError("OSD segmentation needs a separate declared OSD data contract")
        return self


class LanguageDataV1(Contract):
    language: Text
    sha256: Digest
    byte_count: Annotated[int, Field(gt=0, le=_MAX_DATA_BYTES)]


class OCRProducerV1(Contract):
    engine: Literal["tesseract"] = "tesseract"
    engine_version: Text
    executable_sha256: Digest
    language_data: Annotated[tuple[LanguageDataV1, ...], Field(min_length=1, max_length=4)]
    config_sha256: Digest
    policy_version: Literal["tesseract-crop-tsv-v1"] = _POLICY
    psm: Annotated[int, Field(ge=3, le=13)]
    oem: Annotated[int, Field(ge=0, le=3)]

    @model_validator(mode="after")
    def known_producer(self) -> Self:
        languages = [item.language for item in self.language_data]
        if len(set(languages)) != len(languages) or any(not _LANGUAGE.fullmatch(x) for x in languages):
            raise ValueError("OCR producer must preserve unique ordered language data")
        if self.psm == 12 or not _VERSION.fullmatch("tesseract " + self.engine_version):
            raise ValueError("OCR producer must describe a recognized version and supported segmentation")
        return self


class OCRWordV1(Contract):
    page_num: Literal[1] = 1
    block_num: Annotated[int, Field(ge=1)]
    par_num: Annotated[int, Field(ge=1)]
    line_num: Annotated[int, Field(ge=1)]
    word_num: Annotated[int, Field(ge=1)]
    text: str
    confidence: Annotated[float, Field(ge=0, le=100)] | None
    crop_bbox_xyxy: BBox
    canvas_bbox_xyxy: BBox

    @model_validator(mode="after")
    def valid_word(self) -> Self:
        for box in (self.crop_bbox_xyxy, self.canvas_bbox_xyxy):
            if not 0 <= box[0] < box[2] or not 0 <= box[1] < box[3]:
                raise ValueError("OCR word bounds must have positive area")
        if any(ord(c) < 32 or ord(c) == 127 for c in self.text):
            raise ValueError("OCR words must not contain TSV control characters")
        return self


class OCRObservationV1(Contract):
    schema_version: Literal[1] = 1
    evidence_kind: Literal["ocr"] = "ocr"
    sample_id: Text
    parent_image_sha256: Digest
    input_fingerprint: Digest
    crop_sha256: Digest
    crop_bbox_xyxy: tuple[int, int, int, int]
    crop_width: int
    crop_height: int
    canvas_width: Literal[720] = 720
    canvas_height: Literal[1280] = 1280
    scale_x: float
    scale_y: float
    producer: OCRProducerV1
    words: tuple[OCRWordV1, ...]
    text_state: Literal["words", "empty"]
    tsv_sha256: Digest

    @field_validator('schema_version', mode='before')
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError('OCR observation version must be an integer')
        return value
    # No aggregate confidence and no pass/fail: each word is an observation.

    @model_validator(mode="after")
    def bound_words(self) -> Self:
        left, top, right, bottom = self.crop_bbox_xyxy
        if not 0 <= left < right <= 720 or not 0 <= top < bottom <= 1280:
            raise ValueError("OCR crop must be inside the original canvas")
        if not 1 <= self.crop_width <= 4096 or not 1 <= self.crop_height <= 4096:
            raise ValueError("OCR crop dimensions must be bounded")
        if self.scale_x != self.crop_width / (right - left) or self.scale_y != self.crop_height / (bottom - top):
            raise ValueError("OCR crop scale must match its canvas transform")
        identities = set()
        for word in self.words:
            x0, y0, x1, y1 = word.crop_bbox_xyxy
            if x1 > self.crop_width or y1 > self.crop_height:
                raise ValueError("OCR word extends outside its input crop")
            expected = (left + x0 / self.scale_x, top + y0 / self.scale_y,
                        left + x1 / self.scale_x, top + y1 / self.scale_y)
            if word.canvas_bbox_xyxy != expected:
                raise ValueError("OCR word canvas coordinates do not match its transform")
            identity = (word.page_num, word.block_num, word.par_num, word.line_num, word.word_num)
            if identity in identities:
                raise ValueError("OCR word identities must be unique")
            identities.add(identity)
        if (self.text_state == "words") != any(word.text for word in self.words):
            raise ValueError("OCR text state must reflect actual observed word text")
        return self


OCRError = Literal[
    "budget_exhausted", "deadline_exhausted", "input_invalid", "tool_unavailable",
    "language_data_invalid", "tool_identity_changed", "version_invalid", "process_failed",
    "output_limit", "tsv_invalid", "timeout", "cancelled", "local_io_failed", "interrupted",
]


class OCRProcessV1(Contract):
    phase: Literal["version", "ocr"]
    started: bool
    exit_code: int | None
    elapsed_ms: Annotated[float, Field(ge=0)]
    stdout_byte_count: Annotated[int, Field(ge=0)]
    stderr_byte_count: Annotated[int, Field(ge=0)]
    error_code: OCRError | None = None


class OCRAttemptV1(Contract):
    schema_version: Literal[1] = 1
    attempt_index: Annotated[int, Field(ge=0)]
    sample_id: Text
    input_fingerprint: Digest
    parent_image_sha256: Digest
    crop_sha256: Digest | None = None
    config_sha256: Digest
    executable_sha256: Digest | None = None
    engine_version: Text | None = None
    language_data: tuple[LanguageDataV1, ...] = ()
    status: Literal["observed", "unavailable", "failed", "cancelled", "budget_refused"]
    error_code: OCRError | None = None
    suggested_action: Text | None = None
    elapsed_ms: Annotated[float, Field(ge=0)]
    actual_process_starts: Annotated[int, Field(ge=0, le=2)]
    processes: tuple[OCRProcessV1, ...] = ()
    observation: OCRObservationV1 | None = None

    @field_validator('schema_version', mode='before')
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError('OCR attempt version must be an integer')
        return value

    @model_validator(mode="after")
    def consistent_outcome(self) -> Self:
        if (self.status == "observed") != (self.observation is not None and self.error_code is None):
            raise ValueError("OCR observation requires a successful bounded attempt")
        if self.actual_process_starts != sum(x.started for x in self.processes):
            raise ValueError("OCR actual process count must match its process traces")
        if self.observation is not None and (
            self.observation.sample_id != self.sample_id
            or self.observation.input_fingerprint != self.input_fingerprint
            or self.observation.parent_image_sha256 != self.parent_image_sha256
            or self.observation.crop_sha256 != self.crop_sha256
            or self.observation.producer.config_sha256 != self.config_sha256
            or self.observation.producer.executable_sha256 != self.executable_sha256
            or self.observation.producer.engine_version != self.engine_version
            or self.observation.producer.language_data != self.language_data
        ):
            raise ValueError("OCR success evidence must match the attempted input and producer")
        return self


_ACTIONS = {
    "budget_exhausted": "Return the existing OCR attempts to A1; do not retry in this audit.",
    "deadline_exhausted": "Return the tool deadline and attempts to A1; use a new explicit audit to retry.",
    "input_invalid": "Ask A1 for the current full poster and a crop derived from those exact bytes and inputs.",
    "tool_unavailable": "Configure an existing Tesseract executable explicitly; do not install or fall back automatically.",
    "language_data_invalid": "Provide complete regular traineddata files for the declared languages and preserve their order.",
    "tool_identity_changed": "Freeze the selected tool binary during the audit and run a new explicit audit.",
    "version_invalid": "Select a Tesseract executable with a readable version response.",
    "process_failed": "Check the selected engine, language data, and PSM/OEM compatibility outside the audit.",
    "output_limit": "Reduce the explicit crop or revise the declared output limit in a new audit.",
    "tsv_invalid": "Check the selected Tesseract TSV compatibility; keep text evidence unknown.",
    "timeout": "Return the timeout and actual process attempts to A1; do not retry automatically.",
    "cancelled": "Retain the cancelled attempt and start a new explicit audit if needed.",
    "local_io_failed": "Check local read/write permissions for the selected tool and temporary evidence directory.",
    "interrupted": "The local OCR invocation was interrupted; retain its actual process records before starting a new audit.",
}


class OCRBudget:
    """One audit's tool budget. Failed preparations also consume an attempt.

    ``deadline`` is an optional absolute time.monotonic() deadline inherited
    from the enclosing audit. It can shorten but never extend this budget.
    Cancellation remains visible in ``attempts`` before it is propagated.
    """

    def __init__(self, *, max_attempts: int = 2, total_timeout_seconds: float = 20.0,
                 deadline: float | None = None):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 8:
            raise InputError("OCR maximum attempts must be between one and eight")
        if type(total_timeout_seconds) not in (int, float) or not math.isfinite(total_timeout_seconds) or not 0 < total_timeout_seconds <= 300:
            raise InputError("OCR total deadline must be finite and bounded")
        if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise InputError("OCR inherited deadline must be finite")
        self.max_attempts = max_attempts
        own_deadline = time.monotonic() + total_timeout_seconds
        self.deadline = min(own_deadline, deadline) if deadline is not None else own_deadline
        self._claimed = 0
        self._fingerprint: str | None = None
        self._attempts: list[OCRAttemptV1] = []

    @property
    def attempts(self) -> tuple[OCRAttemptV1, ...]:
        return tuple(self._attempts)

    @property
    def attempts_used(self) -> int:
        return self._claimed

    def _claim(self, fingerprint: str) -> tuple[int, OCRError | None]:
        if self._fingerprint is not None and self._fingerprint != fingerprint:
            raise InputError("an OCR budget belongs to exactly one complete audit input")
        self._fingerprint = fingerprint
        if self._claimed >= self.max_attempts:
            return 0, "budget_exhausted"
        if time.monotonic() >= self.deadline:
            return 0, "deadline_exhausted"
        self._claimed += 1
        return self._claimed, None


class _OCRFailure(Exception):
    def __init__(self, code: OCRError):
        self.code = code


def _read_regular(path: Path, limit: int, code: OCRError, deadline: float | None = None) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise _OCRFailure(code)
            chunks = bytearray()
            while len(chunks) <= limit:
                if deadline is not None and time.monotonic() >= deadline:
                    raise _OCRFailure("deadline_exhausted")
                chunk = file.read(min(256 * 1024, limit + 1 - len(chunks)))
                if not chunk:
                    break
                chunks.extend(chunk)
            data = bytes(chunks)
        if not data or len(data) > limit:
            raise _OCRFailure(code)
        return data
    except OSError:
        raise _OCRFailure(code) from None


def _write_private_snapshot(path: Path, data: bytes, *, mode=0o600) -> None:
    """Create only inside the caller's new private temporary directory."""
    fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),mode)
    with os.fdopen(fd,'wb') as handle:
        handle.write(data)
        handle.flush()
        os.fchmod(handle.fileno(),mode)


def parse_tesseract_tsv(data: bytes, evidence: CropEvidenceV1, *, max_words: int = 5000) -> tuple[OCRWordV1, ...]:
    """Parse one PNG page strictly. Empty word text stays empty; -1 stays null.

    Structural rows have -1 confidence and no text. Header-only/structural-only
    responses represent no recognized words, not proof of missing copy.
    """
    if type(max_words) is not int or not 1 <= max_words <= 10000:
        raise InputError("OCR word limit must be a bounded positive integer")
    if not isinstance(evidence, CropEvidenceV1) or not isinstance(data, bytes):
        raise InputError("OCR TSV requires bytes and current crop evidence")
    try:
        evidence = CropEvidenceV1.model_validate({**evidence.model_dump(mode="python"), "data": evidence.data})
        lines = data.decode("utf-8", errors="strict").split("\n")
        if lines[-1] == "":
            lines.pop()
        lines = [line[:-1] if line.endswith("\r") else line for line in lines]
        if not lines or lines[0] != _HEADER:
            raise ValueError
        words: list[OCRWordV1] = []
        identities: set[tuple[int, ...]] = set()
        left, top, _, _ = evidence.crop_bbox_xyxy
        for line in lines[1:]:
            fields = line.split("\t")
            if len(fields) != 12:
                raise ValueError
            if any(not re.fullmatch(r"0|[1-9][0-9]*", x) for x in fields[:10]):
                raise ValueError
            level, page, block, paragraph, row, word, x, y, width, height = map(int, fields[:10])
            if level not in (1, 2, 3, 4, 5) or page != 1:
                raise ValueError
            confidence = float(fields[10])
            if not math.isfinite(confidence) or (confidence != -1 and not 0 <= confidence <= 100):
                raise ValueError
            if width < 1 or height < 1 or x + width > evidence.width or y + height > evidence.height:
                raise ValueError
            text = fields[11]
            if any(ord(c) < 32 or ord(c) == 127 for c in text):
                raise ValueError
            if level < 5:
                if confidence != -1 or text:
                    raise ValueError
                hierarchy = (block, paragraph, row, word)
                if any(value < 1 for value in hierarchy[:level - 1]) or any(hierarchy[level - 1:]):
                    raise ValueError
                continue
            if min(block, paragraph, row, word) < 1:
                raise ValueError
            identity = (page, block, paragraph, row, word)
            if identity in identities or len(words) >= max_words:
                raise ValueError
            identities.add(identity)
            box = (float(x), float(y), float(x + width), float(y + height))
            canvas_box = (left + x / evidence.scale_x, top + y / evidence.scale_y,
                          left + (x + width) / evidence.scale_x, top + (y + height) / evidence.scale_y)
            words.append(OCRWordV1(block_num=block, par_num=paragraph, line_num=row, word_num=word,
                                   text=text, confidence=None if confidence == -1 else confidence,
                                   crop_bbox_xyxy=box, canvas_bbox_xyxy=canvas_box))
        return tuple(words)
    except (UnicodeError, ValueError, TypeError, OverflowError):
        raise InputError("invalid bounded Tesseract TSV observation") from None


class TesseractOCR:
    """Produce optional actual tool evidence with no implicit second attempt."""

    def __init__(self, config: TesseractConfigV1):
        if not isinstance(config, TesseractConfigV1):
            raise InputError("OCR requires TesseractConfigV1")
        self.config = TesseractConfigV1.model_validate(config.model_dump(mode="python"))
        canonical = json.dumps({"config": self.config.model_dump(mode="json"), "policy": _POLICY},
                               sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        self.config_sha256 = hashlib.sha256(canonical).hexdigest()

    async def _process(self, argv: tuple[str, ...], phase: Literal["version", "ocr"],
                       deadline: float, traces: list[OCRProcessV1]) -> bytes:
        start = time.monotonic()
        process = None
        out = bytearray()
        err = bytearray()
        code: OCRError | None = None
        tasks: list[asyncio.Task] = []
        spawn_task: asyncio.Task | None = None

        async def read(stream: asyncio.StreamReader, destination: bytearray, limit: int) -> None:
            while chunk := await stream.read(min(8192, limit + 1 - len(destination))):
                destination.extend(chunk)
                if len(destination) > limit:
                    raise _OCRFailure("output_limit")

        try:
            if time.monotonic() >= deadline:
                raise _OCRFailure("deadline_exhausted")
            # Environment intentionally excludes inherited credentials and OCR
            # configuration variables. The selected tool receives no shell.
            env = {"PATH": os.defpath, "LC_ALL": "C", "OMP_THREAD_LIMIT": "1"}
            async with asyncio.timeout_at(deadline):
                spawn_task = asyncio.create_task(asyncio.create_subprocess_exec(
                    *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
                    start_new_session=os.name == "posix",
                ))
                # If cancellation races with OS spawn, finish acquiring the
                # handle in cleanup so that the child can still be killed.
                process = await asyncio.shield(spawn_task)
                assert process.stdout is not None and process.stderr is not None
                tasks = [asyncio.create_task(read(process.stdout, out, self.config.stdout_max_bytes)),
                         asyncio.create_task(read(process.stderr, err, self.config.stderr_max_bytes))]
                await asyncio.gather(*tasks)
                await process.wait()
                if process.returncode != 0:
                    raise _OCRFailure("process_failed")
            return bytes(out)
        except asyncio.CancelledError:
            code = "cancelled"
            raise
        except TimeoutError:
            code = "timeout"
            raise _OCRFailure(code) from None
        except OSError:
            code = "tool_unavailable"
            raise _OCRFailure(code) from None
        except _OCRFailure as exc:
            code = exc.code
            raise
        except BaseException:
            code = "interrupted"
            raise
        finally:
            if process is None and spawn_task is not None:
                try:
                    process = await asyncio.shield(spawn_task)
                except OSError:
                    pass
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if process is not None and (process.returncode is None or code is not None):
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
            if process is not None:
                # A flooded PIPE can have a paused transport. Drain discarded
                # bytes after killing instead of retaining them or deadlocking
                # wait(). The process group owns its pipes on POSIX.
                async def discard(stream: asyncio.StreamReader) -> None:
                    while await stream.read(8192):
                        pass
                drains = [discard(stream) for stream in (process.stdout, process.stderr) if stream is not None]
                if drains:
                    await asyncio.gather(*drains)
                await process.wait()
            traces.append(OCRProcessV1(
                phase=phase, started=process is not None,
                exit_code=process.returncode if process is not None else None,
                elapsed_ms=(time.monotonic() - start) * 1000,
                stdout_byte_count=len(out), stderr_byte_count=len(err), error_code=code,
            ))

    async def observe(self, audit: AuditInputV1, evidence: CropEvidenceV1,
                      budget: OCRBudget) -> OCRAttemptV1:
        """Recheck current parent pixels and return the outcome, even on failure.

        External cancellation is propagated after the failed/cancelled attempt
        is retained in ``budget.attempts`` and the subprocess is killed/reaped.
        """
        if not isinstance(budget, OCRBudget):
            raise InputError("OCR requires the enclosing audit's OCRBudget")
        fingerprint = input_fingerprint(audit)
        attempt_index, refused = budget._claim(fingerprint)
        start = time.monotonic()
        deadline = min(budget.deadline, start + self.config.timeout_seconds)
        traces: list[OCRProcessV1] = []
        observation = None
        crop_hash = None
        binary_hash = None
        engine_version = None
        language_data: list[LanguageDataV1] = []
        code = refused
        status: Literal["observed", "unavailable", "failed", "cancelled", "budget_refused"] = "budget_refused" if refused else "failed"
        cancelled = False
        interrupted = None
        try:
            if refused:
                raise _OCRFailure(refused)
            try:
                evidence = validate_crop_evidence(evidence, audit)
                crop_hash = evidence.sha256
            except (InputError, ValueError, TypeError, AttributeError):
                raise _OCRFailure("input_invalid") from None
            executable = Path(self.config.executable)
            binary = _read_regular(executable, _MAX_BINARY_BYTES, "tool_unavailable", deadline)
            binary_hash = hashlib.sha256(binary).hexdigest()
            if not os.access(executable, os.X_OK):
                raise _OCRFailure("tool_unavailable")
            with tempfile.TemporaryDirectory(prefix="a4-ocr-") as temporary:
                directory = Path(temporary).resolve(strict=True)
                binary_snapshot = directory/'a4-tesseract-snapshot'
                _write_private_snapshot(binary_snapshot,binary,mode=0o700)
                for language in self.config.languages:
                    if time.monotonic() >= deadline:
                        raise _OCRFailure("deadline_exhausted")
                    data = _read_regular(Path(self.config.tessdata_dir) / f"{language}.traineddata",
                                         _MAX_DATA_BYTES, "language_data_invalid", deadline)
                    target = directory / f"{language}.traineddata"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _write_private_snapshot(target,data)
                    language_data.append(LanguageDataV1(language=language, sha256=hashlib.sha256(data).hexdigest(), byte_count=len(data)))
                # The checked-in Tesseract TSV config is precisely this line;
                # an isolated explicit config avoids undeclared local overrides.
                config_path = directory / "a4-tsv"
                config_bytes = b"tessedit_create_tsv 1\n"
                _write_private_snapshot(config_path,config_bytes)
                image_path = directory / "crop.png"
                _write_private_snapshot(image_path,evidence.data)
                version_data = await self._process((str(binary_snapshot), "--version"), "version", deadline, traces)
                try:
                    match = _VERSION.fullmatch(version_data.decode("utf-8").splitlines()[0])
                except (UnicodeError, IndexError):
                    match = None
                if match is None:
                    raise _OCRFailure("version_invalid")
                engine_version = match[1]
                if _read_regular(binary_snapshot,_MAX_BINARY_BYTES,'tool_identity_changed',deadline) != binary:
                    raise _OCRFailure('tool_identity_changed')
                tsv = await self._process((str(binary_snapshot), str(image_path), "stdout", "--tessdata-dir", str(directory),
                                           "-l", "+".join(self.config.languages), "--psm", str(self.config.psm),
                                           "--oem", str(self.config.oem), str(config_path)), "ocr", deadline, traces)
                if (_read_regular(executable,_MAX_BINARY_BYTES,'tool_identity_changed',deadline) != binary
                        or _read_regular(binary_snapshot,_MAX_BINARY_BYTES,'tool_identity_changed',deadline) != binary
                        or _read_regular(image_path,len(evidence.data),'tool_identity_changed',deadline) != evidence.data
                        or _read_regular(config_path,len(config_bytes),'tool_identity_changed',deadline) != config_bytes):
                    raise _OCRFailure("tool_identity_changed")
                for selected in language_data:
                    current = _read_regular(directory/f'{selected.language}.traineddata',_MAX_DATA_BYTES,
                                            'tool_identity_changed',deadline)
                    if hashlib.sha256(current).hexdigest() != selected.sha256 or len(current) != selected.byte_count:
                        raise _OCRFailure('tool_identity_changed')
                try:
                    words = parse_tesseract_tsv(tsv, evidence, max_words=self.config.max_words)
                except InputError:
                    raise _OCRFailure("tsv_invalid") from None
                if time.monotonic() >= deadline:
                    raise _OCRFailure("deadline_exhausted")
                observation = OCRObservationV1(
                    sample_id=evidence.sample_id, parent_image_sha256=evidence.parent_image_sha256,
                    input_fingerprint=evidence.input_fingerprint, crop_sha256=evidence.sha256,
                    crop_bbox_xyxy=evidence.crop_bbox_xyxy, crop_width=evidence.width, crop_height=evidence.height,
                    scale_x=evidence.scale_x, scale_y=evidence.scale_y,
                    producer=OCRProducerV1(engine_version=match[1], executable_sha256=binary_hash,
                                           language_data=tuple(language_data), config_sha256=self.config_sha256,
                                           psm=self.config.psm, oem=self.config.oem),
                    words=words, text_state="words" if any(word.text for word in words) else "empty",
                    tsv_sha256=hashlib.sha256(tsv).hexdigest(),
                )
            status, code = "observed", None
        except asyncio.CancelledError:
            status, code, cancelled = "cancelled", "cancelled", True
        except _OCRFailure as exc:
            code = exc.code
            if code == "tool_unavailable":
                status = "unavailable"
        except OSError:
            code = "local_io_failed"
        except BaseException as exc:
            interrupted = exc
            status = "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed"
            code, observation = "interrupted", None
        attempt = OCRAttemptV1(
            attempt_index=attempt_index, sample_id=audit.sample_id, input_fingerprint=fingerprint,
            parent_image_sha256=audit.image.sha256, crop_sha256=crop_hash,
            config_sha256=self.config_sha256, status=status, error_code=code,
            executable_sha256=binary_hash, engine_version=engine_version, language_data=tuple(language_data),
            suggested_action=_ACTIONS.get(code) if code else None,
            elapsed_ms=(time.monotonic() - start) * 1000, actual_process_starts=sum(x.started for x in traces),
            processes=tuple(traces), observation=observation,
        )
        budget._attempts.append(attempt)
        if interrupted is not None:
            raise interrupted
        if cancelled:
            raise asyncio.CancelledError
        return attempt

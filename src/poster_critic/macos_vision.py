"""Explicit local macOS Vision OCR, bounded and bound to actual crop pixels.

An OS recognition model is not a truth oracle. The bridge never receives
approved text or custom words. Compilation is explicit setup, not audit work.
The explicitly configured helper is a caller-trusted local executable. Its
hashes establish byte integrity, not authentication or proof of compilation.
https://developer.apple.com/documentation/vision/vnrecognizetextrequest
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from importlib.resources import files
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import stat
import tempfile
import time
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from .contracts import AuditInputV1, BBox, Contract, Text
from .errors import InputError
from .evidence import CropEvidenceV1, Digest, crop_evidence, validate_crop_evidence
from .identity import fingerprint, input_fingerprint


def _vision_normalized_bbox(box: BBox) -> tuple[BBox, bool]:
    """Preserve raw coordinates and correct only <=1e-9 boundary roundoff."""
    if any(not math.isfinite(x) or not -1e-9 <= x <= 1 + 1e-9 for x in box):
        raise ValueError('Vision coordinates exceed the declared roundoff policy')
    normalized = tuple(min(1.0, max(0.0, x)) for x in box)
    x0, y0, x1, y1 = normalized
    if not x0 < x1 or not y0 < y1:
        raise ValueError('Vision normalized box must retain positive area')
    return normalized, normalized != box


class VisionLineV1(Contract):
    text: Text
    confidence: Annotated[float, Field(ge=0, le=1)]
    crop_bbox_xyxy: BBox
    canvas_bbox_xyxy: BBox
    raw_bbox_bottom_left: BBox | None = None
    coordinate_adjustment: Literal['none', 'boundary_roundoff'] = 'none'

    @model_validator(mode='after')
    def bounded_geometry(self) -> Self:
        x0, y0, x1, y1 = self.crop_bbox_xyxy
        if not 0 <= x0 < x1 or not 0 <= y0 < y1:
            raise ValueError('Vision local line box must have positive bounded coordinates')
        x0, y0, x1, y1 = self.canvas_bbox_xyxy
        if not 0 <= x0 < x1 <= 720 or not 0 <= y0 < y1 <= 1280:
            raise ValueError('Vision canvas line box must lie within the full poster')
        if self.coordinate_adjustment == 'boundary_roundoff' and self.raw_bbox_bottom_left is None:
            raise ValueError('Vision boundary roundoff must preserve original coordinates')
        return self


class VisionProducerV1(Contract):
    engine: Literal['apple_vision'] = 'apple_vision'
    request_revision: Literal[3] = 3
    os_version: Text
    languages: tuple[Text, ...]
    language_correction: Literal[False] = False
    recognition_level: Literal['accurate'] = 'accurate'

    @field_validator('request_revision', mode='before')
    @classmethod
    def exact_revision(cls, value):
        if type(value) is not int:
            raise ValueError('Vision request revision must be an integer')
        return value

    @field_validator('language_correction', mode='before')
    @classmethod
    def exact_boolean(cls, value):
        if type(value) is not bool:
            raise ValueError('Vision language correction must be a boolean')
        return value

    @model_validator(mode='after')
    def unique_languages(self) -> Self:
        if not self.languages or len(set(self.languages)) != len(self.languages):
            raise ValueError('Vision producer languages must be explicit and unique')
        return self


class VisionHelperV1(Contract):
    schema_version: Literal[1] = 1
    kind: Literal['a4_vision_helper'] = 'a4_vision_helper'
    source_sha256: Digest
    executable_sha256: Digest
    setup_process_starts: Literal[2] = 2
    producer: VisionProducerV1
    supported_languages: tuple[Text, ...]

    @field_validator('schema_version', 'setup_process_starts', mode='before')
    @classmethod
    def exact_integers(cls, value):
        if type(value) is not int:
            raise ValueError('Vision helper version and setup count must be integers')
        return value

    @model_validator(mode='after')
    def language_coverage(self) -> Self:
        if (not self.supported_languages or len(set(self.supported_languages)) != len(self.supported_languages)
                or not set(self.producer.languages).issubset(self.supported_languages)):
            raise ValueError('Vision helper language coverage must be explicit and unique')
        return self


class VisionConfigV1(Contract):
    schema_version: Literal[1] = 1
    helper_directory: Text
    languages: Annotated[tuple[Text, ...], Field(min_length=1, max_length=4)] = ('en-US',)
    timeout_seconds: Annotated[float, Field(gt=0, le=120)] = 15.0
    max_output_bytes: Annotated[int, Field(ge=1024, le=1024 * 1024)] = 256 * 1024
    max_lines: Annotated[int, Field(ge=1, le=1000)] = 500

    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int:
            raise ValueError('Vision configuration version must be an integer')
        return value

    @model_validator(mode='after')
    def valid_languages(self) -> Self:
        if len(set(self.languages)) != len(self.languages) or any(
            re.fullmatch(r'[a-z]{2,3}(?:-[A-Za-z]{2,4})?', x) is None for x in self.languages
        ):
            raise ValueError('select explicit unique safe Vision language IDs')
        if not Path(self.helper_directory).is_absolute():
            raise ValueError('select an absolute Vision helper directory')
        return self


class VisionObservationV1(Contract):
    schema_version: Literal[1] = 1
    evidence_kind: Literal['ocr_vision'] = 'ocr_vision'
    sample_id: Text
    parent_image_sha256: Digest
    input_fingerprint: Digest
    crop_sha256: Digest
    crop_bbox_xyxy: tuple[int, int, int, int]
    crop_width: Annotated[int, Field(ge=1, le=4096)]
    crop_height: Annotated[int, Field(ge=1, le=4096)]
    producer: VisionProducerV1
    source_sha256: Digest
    executable_sha256: Digest
    config_sha256: Digest
    response_sha256: Digest
    lines: tuple[VisionLineV1, ...]
    text_state: Literal['lines', 'empty']
    # Framework model data is opaque; record OS and request revision, not a fake traineddata hash.
    model_data_sha256: None = None
    coordinate_policy_version: Literal['vision-bbox-epsilon-1e-9-v1'] = 'vision-bbox-epsilon-1e-9-v1'

    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int:
            raise ValueError('Vision observation version must be an integer')
        return value

    @model_validator(mode='after')
    def consistent_observation(self) -> Self:
        left, top, right, bottom = self.crop_bbox_xyxy
        if not 0 <= left < right <= 720 or not 0 <= top < bottom <= 1280:
            raise ValueError('Vision source crop must lie within the full poster')
        if self.crop_width * self.crop_height > 4 * 1024 * 1024:
            raise ValueError('Vision evidence exceeds the crop pixel limit')
        if (self.text_state == 'lines') != bool(self.lines):
            raise ValueError('Vision text state must match the actual line list')
        scale_x = self.crop_width / (right - left)
        scale_y = self.crop_height / (bottom - top)
        for line in self.lines:
            if line.raw_bbox_bottom_left is None:
                raise ValueError('current Vision coordinate policy requires original coordinates for every line')
            x0, y0, x1, y1 = line.crop_bbox_xyxy
            if not 0 <= x0 < x1 <= self.crop_width or not 0 <= y0 < y1 <= self.crop_height:
                raise ValueError('Vision line box must lie within its actual crop dimensions')
            expected = (left + x0 / scale_x, top + y0 / scale_y,
                        left + x1 / scale_x, top + y1 / scale_y)
            if line.canvas_bbox_xyxy != expected:
                raise ValueError('Vision canvas line box must match its exact crop transform')
            if line.raw_bbox_bottom_left is not None:
                normalized, adjusted = _vision_normalized_bbox(line.raw_bbox_bottom_left)
                a, b, c, d = normalized
                local = (a * self.crop_width, (1 - d) * self.crop_height,
                         c * self.crop_width, (1 - b) * self.crop_height)
                if (local != line.crop_bbox_xyxy or (line.coordinate_adjustment == 'boundary_roundoff') != adjusted):
                    raise ValueError('Vision coordinates must match raw box and declared adjustment')
        return self


class VisionAttemptV1(Contract):
    schema_version: Literal[1] = 1
    sample_id: Text
    input_fingerprint: Digest
    config_sha256: Digest
    attempt: Annotated[int, Field(ge=0)]
    status: Literal['observed', 'failed', 'unavailable', 'cancelled', 'budget_refused']
    error_code: Text | None
    elapsed_ms: Annotated[float, Field(ge=0)]
    process_starts: Annotated[int, Field(ge=0, le=1)]
    observation: VisionObservationV1 | None

    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int:
            raise ValueError('Vision attempt version must be an integer')
        return value

    @model_validator(mode='after')
    def consistent_result(self) -> Self:
        if (self.status == 'observed') != (self.observation is not None and self.error_code is None):
            raise ValueError('successful Vision result requires bound observation')
        if self.status != 'observed' and (self.observation is not None or self.error_code is None):
            raise ValueError('failed Vision result requires an error and no observation')
        if self.status == 'observed' and self.process_starts != 1:
            raise ValueError('successful Vision result requires one actual helper process')
        if self.observation is not None and (
            self.sample_id != self.observation.sample_id
            or self.input_fingerprint != self.observation.input_fingerprint
            or self.config_sha256 != self.observation.config_sha256
        ):
            raise ValueError('Vision attempt and observation bindings must agree')
        return self


class VisionBudget:
    """Explicit one-input tool budget. Errors and cancellation remain counted."""
    def __init__(self, *, max_attempts: int = 2, total_timeout_seconds: float = 30.0,
                 deadline: float | None = None):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 8:
            raise InputError('Vision attempt budget must be between one and eight')
        if type(total_timeout_seconds) not in (int, float) or not 0 < total_timeout_seconds <= 300:
            raise InputError('Vision deadline must be finite and bounded')
        if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise InputError('Vision inherited deadline must be finite')
        self.deadline = min(time.monotonic() + total_timeout_seconds, deadline) if deadline is not None else time.monotonic() + total_timeout_seconds
        self.max_attempts = max_attempts
        self._used = 0
        self._input: str | None = None
        self.attempts: list[VisionAttemptV1] = []
        self._busy = False


class _VisionError(Exception):
    def __init__(self, code: str):
        self.code = code


MAX_HELPER_BYTES = 64 * 1024 * 1024
CLEANUP_TIMEOUT_SECONDS = 2.0


def _parent_fd(path: Path) -> int:
    """Anchor absolute path components with no-follow directory descriptors."""
    path = path.expanduser().absolute()
    flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    current = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:-1]:
            replacement = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = replacement
        return current
    except BaseException:
        os.close(current)
        raise


def _regular(path: Path, limit: int, *, executable: bool = False) -> bytes:
    directory = None
    try:
        path = path.expanduser().absolute()
        directory = _parent_fd(path)
        fd = os.open(path.name, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0),
                     dir_fd=directory)
        with os.fdopen(fd, 'rb') as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode) or (executable and not metadata.st_mode & 0o111):
                raise _VisionError('helper_invalid')
            data = handle.read(limit + 1)
        if not data or len(data) > limit:
            raise _VisionError('helper_invalid')
        return data
    except OSError:
        raise _VisionError('helper_unavailable') from None
    finally:
        if directory is not None:
            os.close(directory)


def _write_new(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    directory = _parent_fd(path)
    try:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0),
                     mode, dir_fd=directory)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
    finally:
        os.close(directory)


def _strict_json(raw: bytes, limit: int) -> dict:
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        raise ValueError('Vision JSON requires bounded non-empty bytes')

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('duplicate Vision JSON field')
            value[key] = item
        return value

    def constant(value):
        raise ValueError('non-finite JSON number')

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('non-finite JSON number')
        return result

    value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant,
                       parse_float=finite_float)
    if not isinstance(value, dict):
        raise ValueError('Vision JSON requires an object')
    return value


def _checked_contract(value, cls):
    if not isinstance(value, cls):
        raise InputError(f'Vision requires {cls.__name__}')
    return cls.model_validate_json(value.model_dump_json())


def _kill_group(process) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def _discard(stream) -> None:
    while await stream.read(8192):
        pass


async def _cleanup_process(process, spawn, readers) -> bool:
    """Kill owned group and drain/reap with an independent bounded deadline."""
    deadline = time.monotonic() + CLEANUP_TIMEOUT_SECONDS
    if process is None and spawn is not None:
        done, _ = await asyncio.wait({spawn}, timeout=max(0.0, deadline - time.monotonic()))
        if not done:
            spawn.cancel()
            done, _ = await asyncio.wait({spawn}, timeout=0.1)
        if spawn.done() and not spawn.cancelled():
            try:
                process = spawn.result()
            except BaseException:
                process = None
        elif not spawn.done():
            return False
    if process is not None:
        _kill_group(process)
    for task in readers:
        if not task.done():
            task.cancel()
    if readers:
        await asyncio.wait(readers, timeout=max(0.0, deadline - time.monotonic()))
        for task in readers:
            if task.done() and not task.cancelled():
                task.exception()
    if process is None:
        return True
    drains = [asyncio.create_task(_discard(process.stdout)),
              asyncio.create_task(_discard(process.stderr)),
              asyncio.create_task(process.wait())]
    done, pending = await asyncio.wait(drains, timeout=max(0.0, deadline - time.monotonic()))
    if pending:
        # Close owned transports to escape buffered or inherited pipe deadlocks.
        process._transport.close()
        for task in pending:
            task.cancel()
        more_done, more_pending = await asyncio.wait(pending, timeout=0.1)
        done.update(more_done)
        for task in done:
            if not task.cancelled():
                task.exception()
        return not more_pending and process.returncode is not None
    return all(task.cancelled() or task.exception() is None for task in done) and process.returncode is not None


async def _process(argv: tuple[str, ...], *, deadline: float, limit: int,
                   starts: list[int]) -> bytes:
    if (not isinstance(argv, tuple) or not argv or any(not isinstance(x, str) or not x or '\0' in x for x in argv)
            or type(limit) is not int or limit <= 0
            or type(deadline) not in (int, float) or not math.isfinite(deadline)):
        raise _VisionError('process_config_invalid')
    process, spawn, tasks = None, None, []
    output, error = bytearray(), bytearray()
    cancelled = False

    async def launch():
        result = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={'PATH': os.defpath, 'LC_ALL': 'C'}, start_new_session=True,
        )
        starts.append(1)
        return result

    async def read(stream, destination: bytearray, bound: int) -> None:
        while chunk := await stream.read(8192):
            if len(destination) + len(chunk) > bound:
                raise _VisionError('output_limit')
            destination.extend(chunk)
    try:
        if time.monotonic() >= deadline:
            raise _VisionError('deadline_exhausted')
        async with asyncio.timeout_at(deadline):
            spawn = asyncio.create_task(launch())
            process = await asyncio.shield(spawn)
            tasks = [asyncio.create_task(read(process.stdout, output, limit)),
                     asyncio.create_task(read(process.stderr, error, 64 * 1024))]
            await asyncio.gather(*tasks)
            await process.wait()
            if process.returncode != 0:
                raise _VisionError('process_failed')
            return bytes(output)
    except asyncio.CancelledError:
        cancelled = True
        raise
    except TimeoutError:
        raise _VisionError('timeout') from None
    except OSError:
        raise _VisionError('tool_unavailable') from None
    finally:
        cleanup = asyncio.create_task(_cleanup_process(process, spawn, tasks))
        late_cancel = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                late_cancel = True
        complete = cleanup.result()
        if late_cancel and not cancelled:
            raise asyncio.CancelledError
        if not complete and not cancelled:
            raise _VisionError('process_cleanup_failed')

async def build_vision_helper(out: Path, *, timeout_seconds: float = 60.0) -> VisionHelperV1:
    if platform.system() != 'Darwin':
        raise InputError('Apple Vision helper requires macOS with Swift command-line tools')
    if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 300:
        raise InputError('Vision build deadline must be finite and bounded')
    out = out.expanduser().absolute()
    if any(p.is_symlink() for p in (out, *out.parents)):
        raise InputError('Vision build path must not contain symlinks')
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    _write_new(out / '.incomplete', b'a4_vision_setup\n')
    source = files('poster_critic').joinpath('resources/vision_ocr.swift').read_bytes()
    starts: list[int] = []
    deadline = time.monotonic() + timeout_seconds
    try:
        with tempfile.TemporaryDirectory(prefix='a4-vision-build-') as temp:
            private = Path(temp).resolve(strict=True)
            source_path, binary_path = private / 'vision_ocr.swift', private / 'a4-vision'
            _write_new(source_path, source)
            if _regular(source_path, MAX_HELPER_BYTES) != source:
                raise _VisionError('source_changed')
            await _process(('/usr/bin/xcrun', 'swiftc', '-O', '-module-cache-path', str(private / 'module-cache'),
                            str(source_path), '-o', str(binary_path)),
                           deadline=deadline, limit=1024 * 1024, starts=starts)
            if (_regular(source_path, MAX_HELPER_BYTES) != source
                    or files('poster_critic').joinpath('resources/vision_ocr.swift').read_bytes() != source):
                raise _VisionError('source_changed')
            binary = _regular(binary_path, MAX_HELPER_BYTES, executable=True)
            probe_path = private / 'probe-snapshot'
            _write_new(probe_path, binary, mode=0o700)
            raw = await _process((str(probe_path), 'probe', 'unused', 'en-US'),
                                 deadline=deadline, limit=256 * 1024, starts=starts)
            value = _strict_json(raw, 256 * 1024)
            if (set(value) != {'schema_version', 'producer', 'supported_languages', 'lines'}
                    or type(value['schema_version']) is not int or value['schema_version'] != 1
                    or value['lines'] != [] or not isinstance(value['supported_languages'], list)):
                raise ValueError('invalid Vision setup probe')
            producer = VisionProducerV1.model_validate_json(json.dumps(value['producer']))
            if producer.languages != ('en-US',) or len(starts) != 2:
                raise ValueError('invalid Vision setup identity')
            if (_regular(probe_path, MAX_HELPER_BYTES, executable=True) != binary
                    or _regular(binary_path, MAX_HELPER_BYTES, executable=True) != binary
                    or _regular(source_path, MAX_HELPER_BYTES) != source
                    or files('poster_critic').joinpath('resources/vision_ocr.swift').read_bytes() != source):
                raise _VisionError('tool_identity_changed')
            helper = VisionHelperV1(source_sha256=hashlib.sha256(source).hexdigest(),
                                    executable_sha256=hashlib.sha256(binary).hexdigest(),
                                    producer=producer, supported_languages=tuple(value['supported_languages']))
            _write_new(out / 'vision_ocr.swift', source)
            _write_new(out / 'a4-vision', binary, mode=0o700)
            _write_new(out / 'helper.json', helper.model_dump_json(indent=2).encode())
            # Re-read the exact published bundle before clearing completion marker.
            _read_vision_helper(out, allow_incomplete=True)
            (out / '.incomplete').unlink()
            return helper
    except (_VisionError, ValueError, KeyError, TypeError, OSError) as exc:
        raise InputError('Vision helper setup failed; incomplete directory retained') from exc


def _read_vision_helper(directory: Path, *, allow_incomplete: bool = False) -> tuple[VisionHelperV1, Path]:
    """Integrity check for an explicitly caller-trusted local executable."""
    try:
        directory = directory.expanduser().absolute()
        if not allow_incomplete and os.path.lexists(directory / '.incomplete'):
            raise _VisionError('helper_incomplete')
        packet = _strict_json(_regular(directory / 'helper.json', 256 * 1024), 256 * 1024)
        helper = VisionHelperV1.model_validate_json(json.dumps(packet))
        executable = directory / 'a4-vision'
        actual = hashlib.sha256(_regular(executable, MAX_HELPER_BYTES, executable=True)).hexdigest()
        source = files('poster_critic').joinpath('resources/vision_ocr.swift').read_bytes()
        published_source = _regular(directory / 'vision_ocr.swift', MAX_HELPER_BYTES)
        if (actual != helper.executable_sha256 or hashlib.sha256(source).hexdigest() != helper.source_sha256
                or published_source != source):
            raise _VisionError('tool_identity_changed')
        return helper, executable
    except (_VisionError, ValueError, OSError, UnicodeError) as exc:
        raise InputError('invalid or changed explicitly selected Vision helper') from exc


def read_vision_helper(directory: Path) -> tuple[VisionHelperV1, Path]:
    """Return identity and path; execution must use a separately checked private copy."""
    return _read_vision_helper(directory)


def _parse_vision_observation(raw: bytes, audit: AuditInputV1, crop: CropEvidenceV1,
                              helper: VisionHelperV1, config: VisionConfigV1,
                              *, pixels_checked: bool) -> VisionObservationV1:
    try:
        config = _checked_contract(config, VisionConfigV1)
        helper = _checked_contract(helper, VisionHelperV1)
        value = _strict_json(raw, config.max_output_bytes)
        if not pixels_checked:
            crop = validate_crop_evidence(crop, audit)
        if set(value) != {'schema_version', 'producer', 'lines'} or type(value['schema_version']) is not int or value['schema_version'] != 1:
            raise ValueError
        producer = VisionProducerV1.model_validate_json(json.dumps(value['producer']))
        if (producer.os_version != helper.producer.os_version or producer.languages != config.languages
                or not set(config.languages).issubset(helper.supported_languages)):
            raise ValueError
        if not isinstance(value['lines'], list) or len(value['lines']) > config.max_lines:
            raise ValueError
        lines = []
        left, top, _, _ = crop.crop_bbox_xyxy
        for item in value['lines']:
            if not isinstance(item, dict) or set(item) != {'text', 'confidence', 'bbox_bottom_left'}:
                raise ValueError
            box = item['bbox_bottom_left']
            if not isinstance(box, list) or len(box) != 4 or any(
                type(x) not in (int, float) or not math.isfinite(x) for x in box
            ):
                raise ValueError
            raw_box = tuple(float(x) for x in box)
            normalized, adjusted = _vision_normalized_bbox(raw_box)
            x0, y0, x1, y1 = normalized
            local = (float(x0 * crop.width), float((1 - y1) * crop.height),
                     float(x1 * crop.width), float((1 - y0) * crop.height))
            if not (0 <= local[0] < local[2] <= crop.width and 0 <= local[1] < local[3] <= crop.height):
                raise ValueError
            canvas = (left + local[0] / crop.scale_x, top + local[1] / crop.scale_y,
                      left + local[2] / crop.scale_x, top + local[3] / crop.scale_y)
            if not (0 <= canvas[0] < canvas[2] <= 720 and 0 <= canvas[1] < canvas[3] <= 1280):
                raise ValueError
            lines.append(VisionLineV1(text=item['text'], confidence=item['confidence'],
                crop_bbox_xyxy=local, canvas_bbox_xyxy=canvas, raw_bbox_bottom_left=raw_box,
                coordinate_adjustment='boundary_roundoff' if adjusted else 'none'))
        return VisionObservationV1(sample_id=audit.sample_id, parent_image_sha256=audit.image.sha256,
            input_fingerprint=input_fingerprint(audit), crop_sha256=crop.sha256,
            crop_bbox_xyxy=crop.crop_bbox_xyxy, crop_width=crop.width, crop_height=crop.height,
            producer=producer, source_sha256=helper.source_sha256, executable_sha256=helper.executable_sha256,
            config_sha256=fingerprint(config.model_dump(mode='json')), response_sha256=hashlib.sha256(raw).hexdigest(),
            lines=tuple(lines), text_state='lines' if lines else 'empty')
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise InputError('invalid bounded Vision observation') from exc


def parse_vision_observation(raw: bytes, audit: AuditInputV1, crop: CropEvidenceV1,
                             helper: VisionHelperV1, config: VisionConfigV1) -> VisionObservationV1:
    return _parse_vision_observation(raw, audit, crop, helper, config, pixels_checked=False)


class VisionOCR:
    def __init__(self, config: VisionConfigV1):
        self.config = _checked_contract(config, VisionConfigV1)
        self.config_sha256 = fingerprint(self.config.model_dump(mode='json'))

    async def observe(self, audit: AuditInputV1, crop: CropEvidenceV1, budget: VisionBudget) -> VisionAttemptV1:
        key = input_fingerprint(audit)
        if budget._input not in (None, key) or budget._busy:
            raise InputError('Vision budget belongs to one sequential audit input')
        budget._input = key
        started = time.monotonic()
        attempt, status, code, observation = 0, 'failed', None, None
        starts: list[int] = []
        cancelled = False
        interrupted = None
        budget._busy = True
        try:
            config = _checked_contract(self.config, VisionConfigV1)
            if fingerprint(config.model_dump(mode='json')) != self.config_sha256:
                raise InputError('Vision configuration changed after construction')
            if budget._used >= budget.max_attempts:
                status = 'budget_refused'
                raise _VisionError('budget_exhausted')
            if started >= budget.deadline:
                status = 'budget_refused'
                raise _VisionError('deadline_exhausted')
            budget._used += 1
            attempt = budget._used
            if platform.system() != 'Darwin':
                status = 'unavailable'
                raise _VisionError('platform_unsupported')
            crop = validate_crop_evidence(crop, audit)
            helper, executable = read_vision_helper(Path(config.helper_directory))
            binary = _regular(executable, MAX_HELPER_BYTES, executable=True)
            if hashlib.sha256(binary).hexdigest() != helper.executable_sha256:
                raise _VisionError('tool_identity_changed')
            if not set(config.languages).issubset(helper.supported_languages):
                raise _VisionError('language_unsupported')
            with tempfile.TemporaryDirectory(prefix='a4-vision-') as temp:
                private = Path(temp).resolve(strict=True)
                image, snapshot = private / 'crop.png', private / 'a4-vision-snapshot'
                _write_new(image, crop.data)
                _write_new(snapshot, binary, mode=0o700)
                raw = await _process((str(snapshot), 'recognize', str(image), ','.join(config.languages)),
                    deadline=min(budget.deadline, started + config.timeout_seconds),
                    limit=config.max_output_bytes, starts=starts)
                observation = _parse_vision_observation(raw, audit, crop, helper, config, pixels_checked=True)
                final_helper, _ = read_vision_helper(Path(config.helper_directory))
                if (final_helper != helper or _regular(snapshot, MAX_HELPER_BYTES, executable=True) != binary
                        or _regular(image, len(crop.data)) != crop.data):
                    observation = None
                    raise _VisionError('tool_identity_changed')
                validate_crop_evidence(crop, audit)
                if time.monotonic() >= min(budget.deadline, started + config.timeout_seconds):
                    observation = None
                    raise _VisionError('deadline_exhausted')
            status = 'observed'
        except asyncio.CancelledError:
            code, status, cancelled = 'cancelled', 'cancelled', True
            observation = None
        except _VisionError as exc:
            code = exc.code
            observation = None
        except (InputError, ValueError, OSError):
            code = 'input_or_helper_invalid'
            observation = None
        except BaseException as exc:
            interrupted = exc
            code, status = 'interrupted', 'cancelled' if isinstance(exc, KeyboardInterrupt) else 'failed'
            observation = None
        finally:
            budget._busy = False
        result = VisionAttemptV1(sample_id=audit.sample_id, input_fingerprint=key, config_sha256=self.config_sha256,
            attempt=attempt, status=status, error_code=code, elapsed_ms=(time.monotonic() - started) * 1000,
            process_starts=len(starts), observation=observation)
        budget.attempts.append(result)
        if interrupted is not None:
            raise interrupted
        if cancelled:
            raise asyncio.CancelledError
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Explicit local macOS Vision setup/observation; no model calls')
    sub = parser.add_subparsers(dest='command', required=True)
    build = sub.add_parser('build')
    build.add_argument('--out', required=True, type=Path)
    run = sub.add_parser('observe')
    run.add_argument('--input', required=True, type=Path)
    run.add_argument('--helper', required=True, type=Path)
    run.add_argument('--bbox', required=True, type=int, nargs=4)
    run.add_argument('--languages', nargs='+', default=['en-US'])
    run.add_argument('--out', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'build':
            asyncio.run(build_vision_helper(args.out))
        else:
            from .adapters.local_input import load_input
            out = args.out.expanduser().absolute()
            if any(p.is_symlink() for p in (out, *out.parents)):
                raise InputError('Vision output must not contain symlinks')
            out.mkdir(parents=True, exist_ok=False)
            audit = load_input(args.input)
            crop = crop_evidence(audit, tuple(args.bbox))
            config = VisionConfigV1(helper_directory=str(args.helper.expanduser().absolute()), languages=tuple(args.languages))
            result = asyncio.run(VisionOCR(config).observe(audit, crop, VisionBudget()))
            _write_new(out / 'observation.json', result.model_dump_json(indent=2).encode())
            if result.status != 'observed':
                return 2
        return 0
    except (InputError, OSError, ValueError):
        print('a4_vision_failed; inspect bounded attempt or incomplete setup')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

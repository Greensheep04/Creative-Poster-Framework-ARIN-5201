"""Explicit A4 OCR registration through the existing shared framework tool budget.

The caller chooses the registry, identity, native backend and current-input
authority. Registration here is local software setup, not production registration
or proof that another module has consumed OCR. Arguments contain no expected copy.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import math
import time
from typing import Literal
from uuid import uuid4

from pydantic import field_validator, model_validator
from poster_framework.errors import BudgetExceededError as FrameworkBudgetExceededError
from poster_framework.runtime import RunContext
from poster_framework.tools import ToolDefinition, ToolRegistry
from poster_framework.types import AgentContext, AgentRole, ExecutionIdentity

from ..contracts import AuditInputV1, Contract, Text
from ..dispatch import _validated_input, _validate_image_bytes
from ..errors import InputError
from ..evidence import CropEvidenceV1, Digest, crop_evidence, validate_crop_evidence
from ..identity import fingerprint, input_fingerprint
from ..macos_vision import VisionAttemptV1, VisionBudget, VisionOCR
from ..ocr import OCRAttemptV1, OCRBudget, TesseractOCR


class FrameworkOCRRequestV1(Contract):
    schema_version: Literal[1] = 1
    run_id: Text
    invocation_id: Text
    sample_id: Text
    input_fingerprint: Digest
    parent_image_sha256: Digest
    crop_sha256: Digest
    bbox_xyxy: tuple[int, int, int, int]

    @field_validator('schema_version', mode='before')
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError('OCR request version must be an integer')
        return value

    @field_validator('bbox_xyxy', mode='before')
    @classmethod
    def strict_coordinates(cls, value):
        if type(value) not in (tuple, list) or len(value) != 4 or any(type(x) is not int for x in value):
            raise ValueError('OCR crop coordinates must be four integers')
        return tuple(value)

    @model_validator(mode='after')
    def positive_crop(self):
        left, top, right, bottom = self.bbox_xyxy
        if not 0 <= left < right <= 720 or not 0 <= top < bottom <= 1280:
            raise ValueError('OCR crop must be inside the full poster')
        return self


class FrameworkOCRResultV1(Contract):
    schema_version: Literal[1] = 1
    tool_id: Text
    identity_sha256: Digest
    request_sha256: Digest
    backend_family: Literal['tesseract', 'apple_vision']
    attempt: OCRAttemptV1 | VisionAttemptV1

    @field_validator('schema_version', mode='before')
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError('framework OCR result version must be an integer')
        return value


@dataclass(frozen=True, slots=True)
class FrameworkOCRCallReference:
    identity: ExecutionIdentity
    tool_name: str
    tool_id: str
    request_index: int
    admitted: bool
    outcome: str
    actual_process_starts: int | None


class FrameworkOCRTool:
    """One frozen input and identity, with a persistent non-resetting local budget."""

    def __init__(self, audit: AuditInputV1, identity: ExecutionIdentity, *,
                 backend: TesseractOCR | VisionOCR,
                 current_input: Callable[[AgentContext], AuditInputV1],
                 max_tool_attempts: int = 2, tool_timeout_seconds: float = 30.0):
        if not isinstance(identity, ExecutionIdentity):
            raise InputError('OCR tool requires an explicit framework execution identity')
        self.identity = ExecutionIdentity.model_validate_json(identity.model_dump_json())
        if self.identity.role != AgentRole.quality_critic:
            raise InputError('OCR tool requires the quality_critic role')
        if not isinstance(backend, (TesseractOCR, VisionOCR)) or not callable(current_input):
            raise InputError('OCR tool requires an explicit native backend and current-input authority')
        self.backend_family = 'apple_vision' if isinstance(backend, VisionOCR) else 'tesseract'
        budget_type = VisionBudget if self.backend_family == 'apple_vision' else OCRBudget
        budget_type(max_attempts=max_tool_attempts, total_timeout_seconds=tool_timeout_seconds)
        self.audit = _validated_input(audit)
        _validate_image_bytes(self.audit)
        self._payload = self.audit.model_dump(mode='python')
        self._payload['image']['data'] = self.audit.image.data
        self.input_sha256 = input_fingerprint(self.audit)
        self.identity_sha256 = fingerprint(self.identity.model_dump(mode='json'))
        self.backend = backend
        self.current_input = current_input
        self.max_tool_attempts, self.tool_timeout_seconds = max_tool_attempts, tool_timeout_seconds
        self.config = backend.config
        self.config_sha256 = backend.config_sha256
        self._config_content_sha256 = fingerprint(backend.config.model_dump(mode='json'))
        self.tool_id = str(uuid4())
        self._budget = None
        self._history = ()
        self._budget_policy = None
        self._busy = False
        self._invocations = 0

    @property
    def tool_budget(self):
        return self._budget

    @property
    def records(self):
        return self._history

    def _capture_records(self):
        current = tuple(self._budget.attempts) if self._budget is not None else ()
        if current[:len(self._history)] != self._history:
            raise InputError('OCR enclosing budget attempt records were erased or changed')
        if self._budget is not None:
            used = self._budget._used if self.backend_family == 'apple_vision' else self._budget.attempts_used
            if (type(used) is not int or used < 0 or type(self._budget.max_attempts) is not int
                    or not 1 <= self._budget.max_attempts <= 8 or type(self._budget.deadline) not in (int, float)
                    or not math.isfinite(self._budget.deadline)):
                raise InputError('OCR enclosing budget accounting is invalid')
            if self._budget_policy is not None:
                previous_used, maximum, deadline = self._budget_policy
                if used < previous_used or self._budget.max_attempts > maximum or self._budget.deadline > deadline:
                    raise InputError('OCR enclosing budget cannot be reset or extended')
            self._budget_policy = (used, self._budget.max_attempts, self._budget.deadline)
        self._history = current

    def preflight(self, audit: AuditInputV1, context: AgentContext) -> AuditInputV1:
        if context.identity != self.identity or context.identity.role != AgentRole.quality_critic:
            raise InputError('OCR tool execution run, invocation, role or mode differs')
        checked = _validated_input(audit)
        _validate_image_bytes(checked)
        current = _validated_input(self.current_input(context))
        _validate_image_bytes(current)
        for value in (checked, current, self.audit):
            payload = value.model_dump(mode='python')
            payload['image']['data'] = value.image.data
            if input_fingerprint(value) != self.input_sha256 or payload != self._payload:
                raise InputError('OCR tool input changed or is stale')
        if (self.backend.config != self.config or self.backend.config_sha256 != self.config_sha256
                or fingerprint(self.backend.config.model_dump(mode='json')) != self._config_content_sha256):
            raise InputError('OCR backend configuration changed')
        return checked

    def _adopt_budget(self, budget=None):
        kind = VisionBudget if self.backend_family == 'apple_vision' else OCRBudget
        if budget is not None and not isinstance(budget, kind):
            raise InputError('OCR enclosing budget does not match the selected backend')
        if budget is not None and (type(budget.max_attempts) is not int or not 1 <= budget.max_attempts <= 8
                or type(budget.deadline) not in (int, float) or not math.isfinite(budget.deadline)):
            raise InputError('OCR enclosing budget must have finite bounded limits')
        if self._budget is not None and budget is not None and budget is not self._budget:
            raise InputError('OCR tool budget cannot be reset in the same audit')
        self._capture_records()
        if self._budget is None:
            self._budget = budget or kind(max_attempts=self.max_tool_attempts,
                                          total_timeout_seconds=self.tool_timeout_seconds)
            # Restrict the caller's budget. Never erase records or extend its deadline.
            self._budget.max_attempts = min(self._budget.max_attempts, self.max_tool_attempts)
            self._budget.deadline = min(self._budget.deadline, time.monotonic() + self.tool_timeout_seconds)
            self._capture_records()
        return self._budget

    def _validate_attempt(self, attempt, crop):
        kind = VisionAttemptV1 if self.backend_family == 'apple_vision' else OCRAttemptV1
        if not isinstance(attempt, kind):
            raise InputError('registered OCR tool returned the wrong backend attempt')
        attempt = kind.model_validate_json(attempt.model_dump_json())
        if (attempt.sample_id != self.audit.sample_id or attempt.input_fingerprint != self.input_sha256
                or attempt.config_sha256 != self.config_sha256):
            raise InputError('registered OCR attempt refers to another input or config')
        if isinstance(attempt, OCRAttemptV1) and (attempt.parent_image_sha256 != self.audit.image.sha256
                or attempt.crop_sha256 not in (None, crop.sha256)):
            raise InputError('registered OCR attempt parent/crop binding differs')
        observation = attempt.observation
        if observation is not None and (
                observation.sample_id != self.audit.sample_id
                or observation.parent_image_sha256 != self.audit.image.sha256
                or observation.input_fingerprint != self.input_sha256 or observation.crop_sha256 != crop.sha256
                or observation.crop_bbox_xyxy != crop.crop_bbox_xyxy
                or observation.crop_width != crop.width or observation.crop_height != crop.height):
            raise InputError('registered OCR observation parent/crop binding differs')
        return attempt

    async def invoke(self, request: FrameworkOCRRequestV1, *, context: AgentContext) -> FrameworkOCRResultV1:
        self._invocations += 1  # Framework has already admitted this tool call.
        if self._busy:
            raise InputError('OCR tool cannot be used concurrently')
        request = FrameworkOCRRequestV1.model_validate_json(request.model_dump_json())
        self.preflight(self.audit, context)
        if (request.run_id != self.identity.run_id or request.invocation_id != self.identity.invocation_id
                or request.sample_id != self.audit.sample_id or request.input_fingerprint != self.input_sha256
                or request.parent_image_sha256 != self.audit.image.sha256):
            raise InputError('registered OCR request identity differs')
        crop = crop_evidence(self.audit, request.bbox_xyxy)
        if crop.sha256 != request.crop_sha256:
            raise InputError('registered OCR request crop hash differs from actual pixels')
        budget = self._adopt_budget()
        before = len(self.records)
        self._busy = True
        try:
            try:
                attempt = await self.backend.observe(self.audit, crop, budget)
            except asyncio.CancelledError:
                self._capture_records()
                if len(self.records) <= before or self.records[-1].status != 'cancelled':
                    raise
                # Return a typed cancelled outcome so the existing framework records
                # the call. The outer wrapper propagates cancellation afterward.
                attempt = self.records[-1]
            self.preflight(self.audit, context)
            validate_crop_evidence(crop, self.audit)
            attempt = self._validate_attempt(attempt, crop)
            return FrameworkOCRResultV1(tool_id=self.tool_id, identity_sha256=self.identity_sha256,
                request_sha256=fingerprint(request.model_dump(mode='json')), backend_family=self.backend_family,
                attempt=attempt)
        finally:
            self._busy = False
            self._capture_records()


def register_framework_ocr_tool(registry: ToolRegistry, tool: FrameworkOCRTool, *,
                                name: str = 'a4.ocr.current') -> ToolDefinition:
    """Caller explicitly registers this input-bound local OCR tool in its registry."""
    if not isinstance(registry, ToolRegistry) or not isinstance(tool, FrameworkOCRTool):
        raise InputError('explicit framework registry and A4 OCR tool required')
    return registry.register(name, tool.invoke, description='A4 bounded OCR of the current poster crop; no expected copy.',
        allowed_roles=(AgentRole.quality_critic,), input_model=FrameworkOCRRequestV1,
        output_type=FrameworkOCRResultV1)


class FrameworkOCRBackend:
    """EvidenceReviewer backend whose native attempts pass shared tool admission."""

    def __init__(self, context: RunContext, tool: FrameworkOCRTool, definition: ToolDefinition):
        if not isinstance(context, RunContext) or not isinstance(tool, FrameworkOCRTool):
            raise InputError('framework OCR wrapper requires RunContext and an explicit A4 tool')
        if (not isinstance(definition, ToolDefinition) or getattr(definition.function, '__self__', None) is not tool
                or definition.input_model is not FrameworkOCRRequestV1 or not definition.inject_context
                or definition.allowed_roles != frozenset((AgentRole.quality_critic,))):
            raise InputError('framework OCR definition is not the selected registered tool')
        tool.preflight(tool.audit, context)
        if not any(item.name == definition.name and item.parameters == definition.parameter_schema()
                   for item in context.tools.available_tools()):
            raise InputError('selected OCR tool is unavailable in this framework context; register it explicitly')
        self.context, self.tool, self.definition = context, tool, definition
        self.backend_family = self.effective_backend_family = tool.backend_family
        self.config, self.config_sha256 = tool.config, tool.config_sha256
        self._references = []
        self._busy = False

    @property
    def records(self):
        return self.tool.records

    @property
    def tool_budget(self):
        return self.tool.tool_budget

    @property
    def framework_calls(self):
        return tuple(self._references)

    def preflight(self, audit):
        self.tool.preflight(audit, self.context)

    def _denial(self, audit, crop):
        common = dict(sample_id=audit.sample_id, input_fingerprint=input_fingerprint(audit),
            config_sha256=self.config_sha256, status='budget_refused', error_code='budget_exhausted',
            elapsed_ms=0.0, observation=None)
        if self.backend_family == 'apple_vision':
            return VisionAttemptV1(**common, attempt=0, process_starts=0)
        return OCRAttemptV1(**common, attempt_index=0, parent_image_sha256=audit.image.sha256,
            crop_sha256=crop.sha256, actual_process_starts=0,
            suggested_action='Return shared tool budget exhaustion to A1; do not retry in this audit.')

    async def observe(self, audit, crop, budget):
        if self._busy:
            raise InputError('framework OCR wrapper cannot be shared concurrently')
        self.preflight(audit)
        crop = validate_crop_evidence(crop, audit)
        self.tool._adopt_budget(budget)
        # Tool reconstructs a full-size crop. Do not silently undo a rescale.
        actual = crop_evidence(self.tool.audit, crop.crop_bbox_xyxy)
        if crop.sha256 != actual.sha256 or crop.width != actual.width or crop.height != actual.height:
            raise InputError('framework OCR only accepts unchanged original-pixel crops')
        request = FrameworkOCRRequestV1(run_id=self.context.identity.run_id,
            invocation_id=self.context.identity.invocation_id, sample_id=audit.sample_id,
            input_fingerprint=input_fingerprint(audit), parent_image_sha256=audit.image.sha256,
            crop_sha256=crop.sha256, bbox_xyxy=crop.crop_bbox_xyxy)
        before = self.tool._invocations
        records_before = len(self.records)
        outcome, starts = 'failed', 0
        self._busy = True
        try:
            try:
                result = await self.context.tools.call(self.definition.name, request.model_dump(mode='python'))
            except FrameworkBudgetExceededError:
                outcome = 'denied'
                return self._denial(audit, crop)
            if not isinstance(result, FrameworkOCRResultV1):
                raise InputError('registered framework OCR returned an invalid result')
            result = FrameworkOCRResultV1.model_validate_json(result.model_dump_json())
            if (result.tool_id != self.tool.tool_id or result.identity_sha256 != self.tool.identity_sha256
                    or result.request_sha256 != fingerprint(request.model_dump(mode='json'))
                    or result.backend_family != self.backend_family):
                raise InputError('framework OCR result differs from the selected tool/request')
            self.preflight(audit)
            attempt = self.tool._validate_attempt(result.attempt, crop)
            starts = attempt.process_starts if isinstance(attempt, VisionAttemptV1) else attempt.actual_process_starts
            outcome = attempt.status
            if attempt.status == 'cancelled':
                raise asyncio.CancelledError
            return attempt
        except asyncio.CancelledError:
            outcome = 'cancelled'
            raise
        finally:
            self._busy = False
            if self.tool._invocations > before:
                if len(self.records) > records_before:
                    last = self.records[-1]
                    starts = last.process_starts if isinstance(last, VisionAttemptV1) else last.actual_process_starts
                else:
                    starts = None  # No native accounting record, never infer zero starts.
            self._references.append(FrameworkOCRCallReference(identity=self.tool.identity,
                tool_name=self.definition.name, tool_id=self.tool.tool_id, request_index=len(self._references) + 1,
                admitted=self.tool._invocations > before, outcome=outcome, actual_process_starts=starts))

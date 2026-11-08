"""Bind one candidate to its audit, then invoke one caller-owned output handler.

The caller supplies the real release or A1 handoff operation. Completion here
means that handler returned successfully; it does not mean A1 processed feedback.
Fixed fake observations can exercise feedback paths but cannot release a poster.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from io import BytesIO
from typing import Annotated, Literal, Protocol, Self

from PIL import Image, UnidentifiedImageError
from pydantic import StringConstraints, ValidationError, model_validator

from .adapters.local_input import MAX_IMAGE_BYTES
from .contracts import AuditInputV1, AuditReportV3, Contract
from .errors import InputError
from .identity import input_fingerprint
from .ports import Provider


class AuditRunner(Protocol):
    provider: Provider

    async def audit(self, audit: AuditInputV1) -> AuditReportV3: ...


class DispatchResult(Contract):
    """The full in-memory candidate and report, with bytes excluded from JSON.

    Handlers receive ``pending``. The returned result becomes ``completed`` only
    after awaiting the handler. ``return_to_a1`` records a handoff action, never an
    assertion that the creative director consumed it or completed a repair.
    """

    schema_version: Literal[1] = 1
    route: Literal["release", "a1", "test_only"]
    recipient: Literal["creative_director"] | None
    action: Literal["release_candidate", "return_to_a1", "test_only"]
    handler_status: Literal["pending", "completed", "not_called"]
    report: AuditReportV3
    audit: AuditInputV1
    input_fingerprint: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def consistent_route(self) -> Self:
        expected = {
            "release": ("release_candidate", None),
            "a1": ("return_to_a1", "creative_director"),
            "test_only": ("test_only", None),
        }
        if (self.action, self.recipient) != expected[self.route]:
            raise ValueError("dispatch action and recipient must match the route")
        if (self.handler_status == "not_called") != (self.route == "test_only"):
            raise ValueError("only test-only results omit their output handler")
        if self.route == "a1" and self.report.decision == "pass":
            raise ValueError("passed audits are not feedback handoffs")
        if self.route != "a1" and self.report.decision != "pass":
            raise ValueError("only passed audits can release or produce a test-only result")
        if self.route == "release" and self.report.execution_mode != "live":
            raise ValueError("fake observations cannot release a poster")
        if self.route == "test_only" and self.report.execution_mode != "fake":
            raise ValueError("test-only pass results require fake observations")
        if self.input_fingerprint != input_fingerprint(self.audit):
            raise ValueError("dispatch fingerprint does not match its candidate")
        return self


DispatchHandler = Callable[[DispatchResult], Awaitable[None]]


def _input_payload(audit: AuditInputV1) -> dict:
    if not isinstance(audit, AuditInputV1):
        raise InputError("dispatch requires an AuditInputV1 candidate")
    payload = audit.model_dump(mode="python")
    payload["image"]["data"] = audit.image.data
    return payload


def _validated_input(audit: AuditInputV1) -> AuditInputV1:
    # Pydantic normally trusts existing frozen models; round-trip nested fields
    # so model_copy/object.__setattr__ cannot bypass contract and digest checks.
    try:
        return AuditInputV1.model_validate(_input_payload(audit))
    except (ValidationError, TypeError, AttributeError) as exc:
        raise InputError("dispatch candidate does not satisfy the input contract") from exc


def _validate_image_bytes(audit: AuditInputV1) -> None:
    """Inspect actual pixels once; subsequent binding checks compare exact bytes."""
    if len(audit.image.data) > MAX_IMAGE_BYTES:
        raise InputError("image exceeds the 20 MiB input limit")
    try:
        with Image.open(BytesIO(audit.image.data)) as image:
            if image.format not in ("PNG", "JPEG"):
                raise InputError("candidate image must be a PNG or JPEG")
            if image.size != (720, 1280):
                raise InputError(f"candidate image must be 720x1280, got {image.size}")
            if getattr(image, "n_frames", 1) != 1:
                raise InputError("candidate image must contain a single frame")
            actual_type = "image/png" if image.format == "PNG" else "image/jpeg"
            if actual_type != audit.image.media_type:
                raise InputError("candidate image bytes do not match the declared media_type")
            image.load()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise InputError("cannot decode candidate image bytes") from exc


def _provider_identity(engine: AuditRunner) -> tuple[str, bool]:
    provider = engine.provider
    if type(provider.live) is not bool or not isinstance(provider.name, str) or not provider.name.strip():
        raise InputError("dispatch engine requires a named provider and an explicit live flag")
    return provider.name, provider.live


def _bound_report(report: AuditReportV3, audit: AuditInputV1, *, model: str,
                  live: bool) -> AuditReportV3:
    if not isinstance(report, AuditReportV3):
        raise InputError("dispatch requires the current AuditReportV3 contract")
    try:
        report = AuditReportV3.model_validate(report.model_dump(mode="python"))
    except (ValidationError, TypeError, AttributeError) as exc:
        raise InputError("dispatch report does not satisfy the report contract") from exc
    bindings = (
        # A cached report can share sample/image/field IDs while its underlying
        # requirements changed. Only its original complete-input binding counts;
        # never fill in an absent legacy fingerprint using the current candidate.
        (report.input_fingerprint == input_fingerprint(audit), "input fingerprint"),
        (report.sample_id == audit.sample_id, "sample ID"),
        (report.image_sha256 == audit.image.sha256, "image digest"),
        ({item.keyword_id for item in report.keyword_checks} ==
         {item.keyword_id for item in audit.keywords}, "keyword IDs"),
        ({item.constraint_id for item in report.constraint_checks} ==
         {item.constraint_id for item in audit.user_constraints}, "constraint IDs"),
        ({item.copy_id for item in report.text_checks} ==
         {item.copy_id for item in audit.approved_copy or ()}, "copy IDs"),
        ({item.check_id for item in report.coverage if item.required} ==
         set(audit.required_checks), "required coverage"),
        (report.model == model and report.execution_mode == ("live" if live else "fake"),
         "provider identity"),
        (all(call.model == model and call.live == live for call in report.calls),
         "provider call records"),
    )
    for matches, label in bindings:
        if not matches:
            raise InputError(f"dispatch report does not match candidate {label}")
    if report.decision == "pass":
        required_copy = {item.copy_id for item in audit.approved_copy or () if item.required}
        if (any(item.status != "pass" for item in report.constraint_checks)
                or any(item.readability != "pass" for item in report.text_checks)
                or any(item.copy_id in required_copy and item.status != "pass"
                       for item in report.text_checks)):
            raise InputError("a passed report contradicts its recorded constraint or copy checks")
    if live and report.decision == "pass" and not any(
        call.live and call.status == "ok" and call.transport_status == "succeeded"
        and call.validation_status == "valid" for call in report.calls
    ):
        raise InputError("a live pass requires a valid recorded live observation")
    return report


async def audit_and_dispatch(
    audit: AuditInputV1, engine: AuditRunner, *, on_release: DispatchHandler,
    on_feedback: DispatchHandler,
) -> DispatchResult:
    """Audit and await exactly one output handler; propagate handler failures.

    Release only a bound live pass. Fail/unknown preserve all evidence and input
    context for A1. A fake pass is test-only and calls neither handler. This layer
    never retries handlers because their side effects may already have occurred.
    """
    working = _validated_input(audit)
    _validate_image_bytes(working)
    initial_payload = deepcopy(_input_payload(working))
    initial_fingerprint = input_fingerprint(working)
    provider_identity = _provider_identity(engine)
    report = await engine.audit(working)

    for candidate in (audit, working):
        checked = _validated_input(candidate)
        if (input_fingerprint(checked) != initial_fingerprint
                or _input_payload(checked) != initial_payload):
            raise InputError("dispatch candidate changed while its audit was running")
    if _provider_identity(engine) != provider_identity:
        raise InputError("dispatch provider changed while its audit was running")
    model, live = provider_identity
    report = _bound_report(report, working, model=model, live=live)

    if report.decision == "pass":
        route = "release" if live else "test_only"
        action = "release_candidate" if live else "test_only"
        recipient = None
    else:
        route, action, recipient = "a1", "return_to_a1", "creative_director"
    result = DispatchResult(
        route=route, action=action, recipient=recipient,
        handler_status="not_called" if route == "test_only" else "pending",
        report=report, audit=working, input_fingerprint=initial_fingerprint,
    )
    if route == "test_only":
        return result
    await (on_release if route == "release" else on_feedback)(result)
    return result.model_copy(update={"handler_status": "completed"})

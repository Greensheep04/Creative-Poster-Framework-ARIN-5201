"""A4-owned durable handoff guard; handler return is never a consumer ACK.

SQLite claims are committed before an external handler can run. Interrupted,
failed or timed-out calls remain uncertain until the caller supplies external
evidence. No timeout, stale claim or new output path implicitly permits replay.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import math
from pathlib import Path
import sqlite3
from typing import Annotated, Literal, Self
from uuid import uuid4

from pydantic import Field, StringConstraints, ValidationError, field_validator, model_validator

from .contracts import AuditReportV3, Contract, Status, Text
from .dispatch import (
    DispatchHandler, DispatchResult, _bound_report, _input_payload,
    _validate_image_bytes, _validated_input,
)
from .errors import InputError
from .identity import fingerprint

Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
DeliveryState = Literal["in_flight", "completed", "uncertain", "confirmed_not_applied", "test_only"]
ReconciliationOutcome = Literal["completed", "confirmed_not_applied"]


class DeliveryReportSummaryV1(Contract):
    decision: Status
    model: Text
    prompt_version: Text
    evidence_sha256: Digest


class DeliveryReceiptV1(Contract):
    """Local side-effect state. Completion does not assert A1 consumption."""

    schema_version: Literal[1] = 1
    delivery_key: Digest
    candidate_identity: Text
    input_fingerprint: Digest
    image_sha256: Digest
    execution_mode: Literal["fake", "live"]
    route: Literal["release", "a1", "test_only"]
    action: Literal["release_candidate", "return_to_a1", "test_only"]
    recipient: Literal["creative_director"] | None
    report_sha256: Digest
    report_summary: DeliveryReportSummaryV1
    state: DeliveryState
    attempt: Annotated[int, Field(ge=0)]
    claim_id: Text | None
    created_at: Text
    updated_at: Text
    completion_source: Literal["handler_returned", "external_evidence"] | None = None
    external_evidence: Text | None = None
    problem: Text | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def consistent_state(self) -> Self:
        if self.delivery_key != fingerprint({
            "candidate_identity": self.candidate_identity,
            "input_fingerprint": self.input_fingerprint,
            "image_sha256": self.image_sha256,
            "execution_mode": self.execution_mode,
        }):
            raise ValueError("delivery key must match its candidate/input/mode")
        expected = {
            "release": ("release_candidate", None),
            "a1": ("return_to_a1", "creative_director"),
            "test_only": ("test_only", None),
        }
        if (self.action, self.recipient) != expected[self.route]:
            raise ValueError("receipt route/action/recipient must agree")
        if self.route == "a1" and self.report_summary.decision == "pass":
            raise ValueError("passed reports cannot be A1 feedback")
        if self.route != "a1" and self.report_summary.decision != "pass":
            raise ValueError("only passed reports can release or be test-only")
        if self.route == "release" and self.execution_mode != "live":
            raise ValueError("fake reports cannot release")
        if (self.state == "test_only") != (self.route == "test_only"):
            raise ValueError("test-only receipts cannot claim handler completion")
        if self.route == "test_only":
            if self.execution_mode != "fake" or self.attempt != 0 or self.claim_id is not None:
                raise ValueError("test-only receipts have no production claim")
        elif self.attempt < 1 or self.claim_id is None:
            raise ValueError("handler delivery requires an attempt and claim")
        if (self.state == "completed") != (self.completion_source is not None):
            raise ValueError("only completed delivery has a completion source")
        if self.completion_source == "external_evidence" and self.external_evidence is None:
            raise ValueError("external completion requires supplied evidence")
        if self.state == "confirmed_not_applied" and self.external_evidence is None:
            raise ValueError("confirmed non-application requires supplied evidence")
        return self


class DeliveryError(RuntimeError):
    """Delivery bookkeeping failure; never reinterpret it as poster quality."""

    def __init__(self, message: str, *, receipt: DeliveryReceiptV1 | None = None) -> None:
        super().__init__(message)
        self.receipt = receipt


class DeliveryLedgerError(DeliveryError):
    """The ledger cannot be safely opened, validated or committed."""


class DeliveryConflictError(DeliveryError):
    """The same delivery key was associated with contradictory report evidence."""


class DeliveryAlreadyHandledError(DeliveryError):
    """A previous receipt is completed; the new result was not handled."""


class DeliveryUncertainError(DeliveryError):
    """A handler may have applied its side effect; no implicit replay is safe."""


class DeliveryHandlerTimeoutError(DeliveryError, TimeoutError):
    """Deadline reached; cancellation does not establish non-application."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _checked_result(result: DispatchResult, *, require_pending: bool = True) -> DispatchResult:
    if not isinstance(result, DispatchResult):
        raise InputError("delivery requires a DispatchResult")
    audit = _validated_input(result.audit)
    _validate_image_bytes(audit)
    if not isinstance(result.report, AuditReportV3):
        raise InputError("delivery requires the current AuditReportV3 contract")
    report = _bound_report(result.report, audit, model=result.report.model,
                           live=result.report.execution_mode == "live")
    payload = result.model_dump(mode="python")
    payload["audit"] = _input_payload(audit)
    payload["report"] = report.model_dump(mode="python")
    try:
        checked = DispatchResult.model_validate(payload)
    except (ValidationError, TypeError, AttributeError) as exc:
        raise InputError("delivery result does not satisfy the dispatch contract") from exc
    if require_pending and checked.handler_status == "completed":
        raise InputError("delivery requires a pending result, not an already completed handler")
    return checked


def _new_receipt(result: DispatchResult, candidate_identity: str | None) -> DeliveryReceiptV1:
    identity = result.audit.sample_id if candidate_identity is None else candidate_identity
    if not isinstance(identity, str) or not identity.strip():
        raise InputError("candidate_identity must be a nonempty string")
    # Call timing/accounting can change across repeated equivalent observations.
    # All substantive evidence and the model/prompt remain part of the conflict check.
    substantive = result.report.model_dump(mode="json", exclude={"calls", "vision_calls"})
    summary = DeliveryReportSummaryV1(
        decision=result.report.decision, model=result.report.model,
        prompt_version=result.report.prompt_version, evidence_sha256=fingerprint(substantive),
    )
    bindings = {
        "candidate_identity": identity, "input_fingerprint": result.input_fingerprint,
        "image_sha256": result.audit.image.sha256, "execution_mode": result.report.execution_mode,
    }
    now = _now()
    return DeliveryReceiptV1(
        delivery_key=fingerprint(bindings), **bindings, route=result.route,
        action=result.action, recipient=result.recipient,
        report_sha256=fingerprint(result.report.model_dump(mode="json")), report_summary=summary,
        state="test_only" if result.route == "test_only" else "in_flight",
        attempt=0 if result.route == "test_only" else 1,
        claim_id=None if result.route == "test_only" else uuid4().hex,
        created_at=now, updated_at=now,
    )


def delivery_key(result: DispatchResult, *, candidate_identity: str | None = None) -> str:
    """Compute the validated lookup key without claiming or invoking a handler."""
    return _new_receipt(_checked_result(result, require_pending=False), candidate_identity).delivery_key


class DeliveryGuard:
    """Persist a claim before awaiting one bounded caller-owned handler.

    All callers that need cross-output-path deduplication must share the same
    ledger. SQLite transactions coordinate separate processes. No handler is
    retried here. A cancelled task is retained until it exits; timeout does not
    wait indefinitely for a handler that suppresses cancellation. Synchronous
    work that blocks the event loop cannot be bounded by an async deadline.
    """

    _active_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}

    def __init__(self, ledger_path: Path, *, handler_timeout: float = 30.0) -> None:
        if (isinstance(handler_timeout, bool) or not isinstance(handler_timeout, (int, float))
                or not math.isfinite(handler_timeout) or handler_timeout <= 0):
            raise InputError("handler_timeout must be a finite positive number")
        if not isinstance(ledger_path, Path):
            raise InputError("ledger_path must be a Path")
        if ledger_path.is_symlink():
            raise DeliveryLedgerError("delivery ledger cannot be a symbolic link")
        self.ledger_path = ledger_path.resolve()
        self.handler_timeout = float(handler_timeout)
        self._tasks: set[asyncio.Task[None]] = set()
        # Validate/create upfront so a bad ledger never follows a handler side effect.
        with self._transaction():
            pass

    @property
    def pending_handler_tasks(self) -> tuple[asyncio.Task[None], ...]:
        """Tasks still running after cancellation; callers own loop shutdown."""
        return tuple(self._tasks)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            if self.ledger_path.is_symlink():
                raise DeliveryLedgerError("delivery ledger cannot be a symbolic link")
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.ledger_path, timeout=1.0, isolation_level=None)
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )}
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if not tables and version == 0:
                connection.execute("CREATE TABLE deliveries (delivery_key TEXT PRIMARY KEY, receipt_json TEXT NOT NULL)")
                connection.execute("CREATE TABLE events (event_id INTEGER PRIMARY KEY, delivery_key TEXT NOT NULL, receipt_json TEXT NOT NULL)")
                connection.execute("PRAGMA user_version=1")
            elif tables != {"deliveries", "events"} or version != 1:
                raise DeliveryLedgerError("unsupported or malformed delivery ledger schema")
            for table, expected in (
                ("deliveries", (("delivery_key", "TEXT", 0, 1), ("receipt_json", "TEXT", 1, 0))),
                ("events", (("event_id", "INTEGER", 0, 1), ("delivery_key", "TEXT", 1, 0),
                            ("receipt_json", "TEXT", 1, 0))),
            ):
                columns = tuple((row[1], row[2], row[3], row[5])
                                for row in connection.execute(f"PRAGMA table_info({table})"))
                if columns != expected:
                    raise DeliveryLedgerError("malformed delivery ledger columns")
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger', 'view') LIMIT 1").fetchone():
                raise DeliveryLedgerError("delivery ledger must not contain triggers or views")
            yield connection
            connection.commit()
        except (sqlite3.Error, OSError) as exc:
            if connection is not None:
                connection.rollback()
            raise DeliveryLedgerError("delivery ledger operation failed") from exc
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _read(connection: sqlite3.Connection, delivery_key: str) -> DeliveryReceiptV1 | None:
        rows = connection.execute("SELECT receipt_json FROM deliveries WHERE delivery_key=?",
                                  (delivery_key,)).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise DeliveryLedgerError("delivery ledger has duplicate claims")
        return DeliveryGuard._parse_receipt(rows[0][0], delivery_key)

    @staticmethod
    def _parse_receipt(raw: str, key: str) -> DeliveryReceiptV1:
        try:
            receipt = DeliveryReceiptV1.model_validate_json(raw)
        except (ValidationError, TypeError, ValueError) as exc:
            raise DeliveryLedgerError("delivery ledger contains an invalid receipt") from exc
        if receipt.delivery_key != key or receipt.state == "test_only":
            raise DeliveryLedgerError("delivery ledger receipt binding is invalid")
        return receipt

    @staticmethod
    def _write(connection: sqlite3.Connection, receipt: DeliveryReceiptV1) -> None:
        raw = receipt.model_dump_json()
        connection.execute(
            "INSERT INTO deliveries(delivery_key, receipt_json) VALUES(?, ?) "
            "ON CONFLICT(delivery_key) DO UPDATE SET receipt_json=excluded.receipt_json",
            (receipt.delivery_key, raw),
        )
        connection.execute("INSERT INTO events(delivery_key, receipt_json) VALUES(?, ?)",
                           (receipt.delivery_key, raw))

    def lookup(self, delivery_key: str) -> DeliveryReceiptV1 | None:
        with self._transaction() as connection:
            return self._read(connection, delivery_key)

    def history(self, delivery_key: str) -> tuple[DeliveryReceiptV1, ...]:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT receipt_json FROM events WHERE delivery_key=? ORDER BY event_id", (delivery_key,),
            ).fetchall()
            return tuple(self._parse_receipt(row[0], delivery_key) for row in rows)

    def _claim(self, requested: DeliveryReceiptV1) -> DeliveryReceiptV1:
        with self._transaction() as connection:
            previous = self._read(connection, requested.delivery_key)
            if previous is not None:
                if ((previous.route, previous.action, previous.recipient, previous.report_summary)
                        != (requested.route, requested.action, requested.recipient, requested.report_summary)):
                    raise DeliveryConflictError("same candidate/input/mode has conflicting report evidence",
                                                receipt=previous)
                if previous.state == "completed":
                    raise DeliveryAlreadyHandledError("delivery was already handled; new result was not sent",
                                                       receipt=previous)
                if previous.state in ("in_flight", "uncertain"):
                    raise DeliveryUncertainError("delivery may have applied; external evidence is required",
                                                 receipt=previous)
                requested = requested.model_copy(update={"attempt": previous.attempt + 1})
            self._write(connection, requested)
            return requested

    def _settle(self, claim: DeliveryReceiptV1, *, state: Literal["completed", "uncertain"],
                problem: str | None = None) -> DeliveryReceiptV1:
        with self._transaction() as connection:
            current = self._read(connection, claim.delivery_key)
            if current is None or current.claim_id != claim.claim_id or current.state != "in_flight":
                raise DeliveryUncertainError("delivery claim changed while handler was running", receipt=current)
            updated = current.model_copy(update={
                "state": state, "updated_at": _now(), "problem": problem,
                "completion_source": "handler_returned" if state == "completed" else None,
            })
            self._write(connection, updated)
            return updated

    def reconcile(self, delivery_key: str, *, outcome: ReconciliationOutcome, evidence: str,
                  caller_confirmed: bool = False) -> DeliveryReceiptV1:
        """Apply caller-confirmed external evidence, without invoking any handler.

        ``confirmed_not_applied`` permits a later *explicit* deliver invocation.
        ``completed`` records external completion, never an A1 consumption ACK.
        Process-local still-running handlers prevent reconciliation; cross-process
        callers must ensure their supplied evidence covers any still-active work.
        """
        if caller_confirmed is not True or not isinstance(evidence, str) or not evidence.strip():
            raise InputError("reconciliation requires caller_confirmed=True and nonempty external evidence")
        if outcome not in ("completed", "confirmed_not_applied"):
            raise InputError("unsupported delivery reconciliation outcome")
        active = self._active_tasks.get((str(self.ledger_path), delivery_key))
        if active is not None and not active.done():
            raise DeliveryUncertainError("handler is still running; reconciliation cannot permit replay",
                                         receipt=self.lookup(delivery_key))
        with self._transaction() as connection:
            current = self._read(connection, delivery_key)
            if current is None:
                raise InputError("delivery key is not recorded in this ledger")
            if current.state not in ("in_flight", "uncertain"):
                raise DeliveryConflictError("only interrupted or uncertain delivery can be reconciled", receipt=current)
            updated = current.model_copy(update={
                "state": outcome, "updated_at": _now(), "external_evidence": evidence,
                "completion_source": "external_evidence" if outcome == "completed" else None,
            })
            self._write(connection, updated)
            return updated

    def _track(self, task: asyncio.Task[None], key: str) -> None:
        registry_key = (str(self.ledger_path), key)
        self._tasks.add(task)
        self._active_tasks[registry_key] = task

        def finished(completed: asyncio.Task[None]) -> None:
            self._tasks.discard(completed)
            if self._active_tasks.get(registry_key) is completed:
                self._active_tasks.pop(registry_key, None)
            if not completed.cancelled():
                completed.exception()  # Retrieve late failures; the receipt remains uncertain.

        task.add_done_callback(finished)

    async def deliver(self, result: DispatchResult, handler: DispatchHandler, *,
                      candidate_identity: str | None = None) -> DeliveryReceiptV1:
        """Send a validated pending result once; propagate original handler errors.

        Timeout/cancellation requests cancellation without waiting indefinitely.
        Fake passes return test-only and never run or claim an external handler.
        Fake fail/unknown feedback still uses the guarded caller-provided handler.
        """
        checked = _checked_result(result)
        if not callable(handler):
            raise InputError("delivery handler must be callable")
        requested = _new_receipt(checked, candidate_identity)
        if requested.state == "test_only":
            return requested
        claim = self._claim(requested)
        task = None
        try:
            # Invoke inside a task so even a synchronous exception is accounted for.
            async def invoke() -> None:
                await handler(checked)

            task = asyncio.create_task(invoke())
            self._track(task, claim.delivery_key)
            done, _ = await asyncio.wait({task}, timeout=self.handler_timeout)
            if not done:
                task.cancel()
                receipt = self._settle(claim, state="uncertain", problem="handler_timeout")
                raise DeliveryHandlerTimeoutError("delivery handler exceeded its deadline; outcome is uncertain",
                                                  receipt=receipt)
            await task
        except DeliveryHandlerTimeoutError:
            raise
        except BaseException as exc:
            if task is not None and not task.done():
                task.cancel()
            self._settle(claim, state="uncertain", problem=type(exc).__name__)
            raise
        return self._settle(claim, state="completed")

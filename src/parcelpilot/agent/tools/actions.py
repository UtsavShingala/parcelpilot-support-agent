"""State-changing actions, split into preparing and confirming.

The split is the whole point. ``prepare_*`` is a pure function: it validates what
was asked, resolves it against data the caller may see, and returns a draft. It
writes nothing, touches no ledger, and is safe to call speculatively -- which
matters, because a model exploring a question will sometimes prepare an action it
then decides against.

:meth:`ActionLedger.confirm` is the only thing in this system that writes. It is
deliberately absent from the tool registry, so the model cannot reach it at all. A
confirmation has to arrive from the person, through the transport layer, carrying
the draft they were shown. "Ask before acting" implemented as an instruction is a
request the model may forget under pressure; implemented as an unreachable function
it is a property of the system.

Confirmation re-checks authorisation from scratch rather than trusting the draft.
A draft is just data, and by the time it comes back it has been outside the process.

Two timestamps are recorded. ``effective_at`` is the dataset snapshot, keeping the
ledger on the same timeline as every other date the system reasons about.
``recorded_at`` is real wall-clock time, because when a row was actually written is
an audit fact rather than something to reason about.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from parcelpilot.agent.tools.base import (
    ALL_ROLES,
    INTERNAL_ONLY,
    Tool,
    ToolError,
    ToolPermissionError,
    object_schema,
    string_field,
)
from parcelpilot.auth.context import CallerContext, Role
from parcelpilot.data.queries import OperationalData

SEVERITIES = ("P1", "P2", "P3")


class ActionKind(StrEnum):
    ESCALATION = "escalation"
    TICKET_UPDATE = "ticket_update"
    FOLLOW_UP = "follow_up"


# Who may ultimately perform each action. Checked when the registry is built and
# again at confirmation, because only the second check is enforcement.
ACTION_ROLES: dict[ActionKind, frozenset[Role]] = {
    ActionKind.ESCALATION: ALL_ROLES,
    ActionKind.TICKET_UPDATE: INTERNAL_ONLY,
    ActionKind.FOLLOW_UP: INTERNAL_ONLY,
}


@dataclass(frozen=True)
class ActionDraft:
    """A proposed action. Holding one changes nothing."""

    draft_id: str
    kind: ActionKind
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    account_id: str | None = None
    prepared_for: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "kind": self.kind.value,
            "summary": self.summary,
            "details": self.details,
            "account_id": self.account_id,
            "prepared_for": self.prepared_for,
            "status": "awaiting confirmation",
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ActionDraft:
        """Rebuild a draft from a tool payload.

        Tool payloads stay plain JSON so they serialise cleanly for the model; the
        loop reconstructs the draft from one to hand back for confirmation. What
        comes back is never trusted -- :meth:`ActionLedger.confirm` re-authorises it.
        """
        return cls(
            draft_id=payload["draft_id"],
            kind=ActionKind(payload["kind"]),
            summary=payload["summary"],
            details=dict(payload.get("details") or {}),
            account_id=payload.get("account_id"),
            prepared_for=payload.get("prepared_for", ""),
        )


@dataclass(frozen=True)
class ActionRecord:
    """An action that actually happened."""

    action_id: int
    draft_id: str
    kind: ActionKind
    summary: str
    details: dict[str, Any]
    account_id: str | None
    performed_by: str
    effective_at: str
    recorded_at: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        return payload


def _draft_id(kind: ActionKind, summary: str, details: dict[str, Any]) -> str:
    """A content hash, so preparing the same action twice yields the same draft.

    Confirming a draft id that is already in the ledger is then recognisable as a
    duplicate rather than silently filed a second time.
    """
    payload = json.dumps(
        {"kind": kind.value, "summary": summary, "details": details}, sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# -- preparing (writes nothing) -------------------------------------------------


def prepare_escalation(
    data: OperationalData,
    caller: CallerContext,
    *,
    reason: str,
    severity: str = "P3",
    ticket_id: str | None = None,
    order_id: str | None = None,
) -> ActionDraft:
    """Draft a hand-off to a human. Nothing is written."""
    if severity.upper() not in SEVERITIES:
        raise ToolError(f"severity must be one of {', '.join(SEVERITIES)}")
    if not reason.strip():
        raise ToolError("an escalation needs a reason a human can act on")

    details: dict[str, Any] = {"reason": reason.strip(), "severity": severity.upper()}
    account_id = caller.account_id

    if ticket_id:
        ticket = data.ticket(caller, ticket_id)
        if ticket is None:
            raise ToolError(f"no ticket {ticket_id} is visible to this user")
        details["ticket_id"] = ticket_id
        details["ticket_subject"] = ticket.get("subject")
        account_id = account_id or str(ticket.get("account_id") or "") or None

    if order_id:
        order = data.order(caller, order_id)
        if order is None:
            raise ToolError(f"no order {order_id} is visible to this user")
        details["order_id"] = order_id
        account_id = account_id or str(order.get("account_id") or "") or None

    subject = ticket_id or order_id or "the request"
    summary = f"Escalate {subject} at {details['severity']}: {details['reason']}"
    return ActionDraft(
        draft_id=_draft_id(ActionKind.ESCALATION, summary, details),
        kind=ActionKind.ESCALATION,
        summary=summary,
        details=details,
        account_id=account_id,
        prepared_for=caller.describe(),
    )


def prepare_ticket_update(
    data: OperationalData,
    caller: CallerContext,
    *,
    ticket_id: str,
    status: str | None = None,
    note: str | None = None,
) -> ActionDraft:
    """Draft a change to a ticket. Nothing is written."""
    if not status and not note:
        raise ToolError("a ticket update needs a new status, a note, or both")

    ticket = data.ticket(caller, ticket_id)
    if ticket is None:
        raise ToolError(f"no ticket {ticket_id} is visible to this user")

    details: dict[str, Any] = {"ticket_id": ticket_id, "current_status": ticket.get("status")}
    changes = []
    if status:
        details["new_status"] = status
        changes.append(f"status {ticket.get('status')} -> {status}")
    if note:
        details["note"] = note
        changes.append("add a note")

    summary = f"Update {ticket_id}: {', '.join(changes)}"
    return ActionDraft(
        draft_id=_draft_id(ActionKind.TICKET_UPDATE, summary, details),
        kind=ActionKind.TICKET_UPDATE,
        summary=summary,
        details=details,
        account_id=str(ticket.get("account_id") or "") or None,
        prepared_for=caller.describe(),
    )


def prepare_follow_up(
    data: OperationalData,
    caller: CallerContext,
    *,
    subject: str,
    owner: str,
    account_id: str | None = None,
    due_note: str | None = None,
) -> ActionDraft:
    """Draft a follow-up task for a person. Nothing is written."""
    if not subject.strip():
        raise ToolError("a follow-up task needs a subject")

    if account_id:
        if data.account(caller, account_id) is None:
            raise ToolError(f"no account {account_id} is visible to this user")

    details: dict[str, Any] = {"subject": subject.strip(), "owner": owner.strip()}
    if due_note:
        details["due"] = due_note.strip()

    summary = f"Follow-up for {owner.strip()}: {subject.strip()}"
    return ActionDraft(
        draft_id=_draft_id(ActionKind.FOLLOW_UP, summary, details),
        kind=ActionKind.FOLLOW_UP,
        summary=summary,
        details=details,
        account_id=account_id or caller.account_id,
        prepared_for=caller.describe(),
    )


# -- confirming (the only writer) -----------------------------------------------


class ActionLedger:
    """Append-only record of confirmed actions. The only mutator in the system."""

    def __init__(self, path: Path, *, effective_at: datetime) -> None:
        self._path = path
        self._effective_at = effective_at
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS actions (
                action_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id     TEXT NOT NULL UNIQUE,
                kind         TEXT NOT NULL,
                summary      TEXT NOT NULL,
                details      TEXT NOT NULL,
                account_id   TEXT,
                performed_by TEXT NOT NULL,
                effective_at TEXT NOT NULL,
                recorded_at  TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def confirm(self, draft: ActionDraft, caller: CallerContext) -> ActionRecord:
        """Execute a draft the user has explicitly confirmed.

        Authorisation is re-checked here rather than trusted from the draft: a draft
        is data, and by the time it returns it has been outside this process.
        """
        allowed = ACTION_ROLES.get(draft.kind, frozenset())
        if caller.role not in allowed:
            raise ToolPermissionError(
                f"a {caller.role.value} may not perform a {draft.kind.value}"
            )
        if draft.account_id and not caller.account_scope().permits(draft.account_id):
            raise ToolPermissionError(
                f"this user may not act on account {draft.account_id}"
            )

        existing = self.find(draft.draft_id)
        if existing is not None:
            return existing

        recorded_at = datetime.now(UTC).isoformat()
        cursor = self._connection.execute(
            """
            INSERT INTO actions
                (draft_id, kind, summary, details, account_id,
                 performed_by, effective_at, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft.draft_id,
                draft.kind.value,
                draft.summary,
                json.dumps(draft.details, sort_keys=True),
                draft.account_id,
                caller.describe(),
                self._effective_at.isoformat(),
                recorded_at,
            ),
        )
        self._connection.commit()
        record = self.find(draft.draft_id)
        if record is None:  # pragma: no cover - insert just succeeded
            raise ToolError(f"action {cursor.lastrowid} could not be read back")
        return record

    def find(self, draft_id: str) -> ActionRecord | None:
        row = self._connection.execute(
            "SELECT * FROM actions WHERE draft_id = ?", (draft_id,)
        ).fetchone()
        return _to_record(row) if row else None

    def records(self, caller: CallerContext) -> list[ActionRecord]:
        """Confirmed actions this caller may see, newest first."""
        if caller.is_internal:
            rows = self._connection.execute(
                "SELECT * FROM actions ORDER BY action_id DESC"
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM actions WHERE account_id = ? ORDER BY action_id DESC",
                (caller.account_id,),
            ).fetchall()
        return [_to_record(row) for row in rows]


def _to_record(row: sqlite3.Row) -> ActionRecord:
    return ActionRecord(
        action_id=row["action_id"],
        draft_id=row["draft_id"],
        kind=ActionKind(row["kind"]),
        summary=row["summary"],
        details=json.loads(row["details"]),
        account_id=row["account_id"],
        performed_by=row["performed_by"],
        effective_at=row["effective_at"],
        recorded_at=row["recorded_at"],
    )


# -- tools ----------------------------------------------------------------------


def build_action_tools(data: OperationalData) -> list[Tool]:
    """The prepare tools. Confirmation is deliberately not among them."""

    def prepare_escalation_tool(
        caller: CallerContext,
        *,
        reason: str,
        severity: str = "P3",
        ticket_id: str | None = None,
        order_id: str | None = None,
    ) -> dict[str, Any]:
        draft = prepare_escalation(
            data, caller, reason=reason, severity=severity, ticket_id=ticket_id, order_id=order_id
        )
        return _awaiting(draft)

    def prepare_ticket_update_tool(
        caller: CallerContext,
        *,
        ticket_id: str,
        status: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        draft = prepare_ticket_update(
            data, caller, ticket_id=ticket_id, status=status, note=note
        )
        return _awaiting(draft)

    def prepare_follow_up_tool(
        caller: CallerContext,
        *,
        subject: str,
        owner: str,
        account_id: str | None = None,
        due_note: str | None = None,
    ) -> dict[str, Any]:
        draft = prepare_follow_up(
            data, caller, subject=subject, owner=owner, account_id=account_id, due_note=due_note
        )
        return _awaiting(draft)

    return [
        Tool(
            name="prepare_escalation",
            description=(
                "Draft an escalation to a human support agent. This does NOT escalate "
                "anything: it returns a draft for the user to confirm or reject, and you "
                "must show them what it says and ask. Use it when the request needs human "
                "judgment, when an exception is being asked for that no document supports, "
                "when sources conflict irreconcilably, or when the answer is not in the "
                "documents at all."
            ),
            parameters=object_schema(
                {
                    "reason": string_field(
                        "Why a human is needed, specific enough to act on without "
                        "rereading the conversation."
                    ),
                    "severity": string_field(
                        "Severity per the support policy.", enum=list(SEVERITIES)
                    ),
                    "ticket_id": string_field("Related ticket, if there is one."),
                    "order_id": string_field("Related order, if there is one."),
                },
                required=["reason"],
            ),
            handler=prepare_escalation_tool,
            roles=ACTION_ROLES[ActionKind.ESCALATION],
            mutating=True,
        ),
        Tool(
            name="prepare_ticket_update",
            description=(
                "Draft a change to a ticket's status or add a note. This does NOT change "
                "the ticket: it returns a draft for the user to confirm. Show them the "
                "draft and ask before treating it as done."
            ),
            parameters=object_schema(
                {
                    "ticket_id": string_field("The ticket to update."),
                    "status": string_field("New status, such as open, pending or closed."),
                    "note": string_field("A note to append to the ticket."),
                },
                required=["ticket_id"],
            ),
            handler=prepare_ticket_update_tool,
            roles=ACTION_ROLES[ActionKind.TICKET_UPDATE],
            mutating=True,
        ),
        Tool(
            name="prepare_follow_up",
            description=(
                "Draft a follow-up task for a named person. This does NOT create the "
                "task: it returns a draft for the user to confirm."
            ),
            parameters=object_schema(
                {
                    "subject": string_field("What needs doing."),
                    "owner": string_field("Who should do it."),
                    "account_id": string_field("Account it concerns, if any."),
                    "due_note": string_field("When it is needed, in words."),
                },
                required=["subject", "owner"],
            ),
            handler=prepare_follow_up_tool,
            roles=ACTION_ROLES[ActionKind.FOLLOW_UP],
            mutating=True,
        ),
    ]


def _awaiting(draft: ActionDraft) -> dict[str, Any]:
    return {
        **draft.to_dict(),
        "instruction": (
            "Nothing has been done yet. Show this draft to the user, in their own "
            "terms, and ask them to confirm or reject it. Do not describe it as "
            "completed, submitted or raised."
        ),
    }

"""What needs attention, before anyone asks.

A reactive assistant only helps once a customer writes in. These detectors read the
same data the assistant reads and surface what a support manager would want on a
Monday morning: response targets already missed, one product fault generating
several tickets, and past answers that current documents contradict.

Every signal carries its evidence -- the ticket ids and the clause it rests on --
because an ops view that says "3 issues need attention" without saying which, or
why, gets ignored after the second look.

Three things it deliberately does not do. It does not call a model: a dashboard that
costs money to open does not get opened, and a finding that changes between
refreshes cannot be trusted. It does not measure business-hours targets, because the
corpus defines no working calendar. And it never widens what a caller can see: the
data arrives through the same scoped queries as everything else, so a support agent
sees every account and a customer would see only their own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from parcelpilot.auth.context import CallerContext
from parcelpilot.data.queries import OperationalData
from parcelpilot.ingest.authority import AuthorityTier
from parcelpilot.ingest.documents import HEADER_SECTION_TITLE, Chunk
from parcelpilot.insights.severity import Severity, classify
from parcelpilot.insights.targets import Target, targets_for
from parcelpilot.retrieval.text import content_terms

# A ticket is "approaching" its target once this much of it has elapsed. Early enough
# to act on, late enough not to flag every ticket the moment it arrives.
AT_RISK_FRACTION = 0.75

# Shared terms needed to call two pieces of text the same issue. One is usually a
# topic word every ticket shares.
MATCH_TERMS = 2

_RANK = {"P1": 0, "P2": 1, "P3": 2, "info": 3}


@dataclass(frozen=True)
class Signal:
    """One thing worth a person's attention, with the evidence behind it."""

    kind: str
    severity: str
    title: str
    detail: str
    tickets: tuple[str, ...] = ()
    accounts: tuple[str, ...] = ()
    citations: tuple[str, ...] = ()
    elapsed_minutes: int | None = None
    target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "tickets": list(self.tickets),
            "accounts": list(self.accounts),
            "citations": list(self.citations),
            "elapsed_minutes": self.elapsed_minutes,
            "target": self.target,
        }


@dataclass
class _Ticket:
    """A ticket with everything the detectors need already worked out."""

    row: dict[str, Any]
    severity: Severity
    elapsed: int
    target: Target | None = field(default=None)

    @property
    def ticket_id(self) -> str:
        return str(self.row["ticket_id"])

    @property
    def account_id(self) -> str:
        return str(self.row["account_id"])

    @property
    def is_open(self) -> bool:
        return str(self.row.get("status", "")).lower() == "open"

    @property
    def text(self) -> str:
        return f"{self.row.get('subject', '')} {self.row.get('description', '')}"


def detect(
    data: OperationalData, caller: CallerContext, chunks: list[Chunk]
) -> list[Signal]:
    """Everything worth flagging to ``caller``, most urgent first."""
    snapshot = data.snapshot_at
    plans = {
        str(account["account_id"]): str(account.get("plan") or "")
        for account in data.accounts(caller)
    }
    tickets = [
        _prepare(row, snapshot, chunks, plans.get(str(row["account_id"])))
        for row in data.tickets(caller)
    ]

    signals = [
        *_sla_signals(tickets),
        *_issue_clusters(tickets, chunks),
        *_unverified_guidance(tickets, chunks),
    ]
    return sorted(signals, key=lambda signal: (_RANK.get(signal.severity, 9), signal.title))


def _prepare(
    row: dict[str, Any], snapshot: datetime, chunks: list[Chunk], plan: str | None
) -> _Ticket:
    severity = classify(str(row.get("subject", "")), str(row.get("description") or ""))
    created = datetime.fromisoformat(str(row["created_at"]))
    elapsed = int((snapshot - created).total_seconds() // 60)
    targets = targets_for(chunks, account_id=str(row["account_id"]), plan=plan)
    return _Ticket(row=row, severity=severity, elapsed=elapsed, target=targets.get(severity.level))


# -- response targets -----------------------------------------------------------


def _sla_signals(tickets: list[_Ticket]) -> list[Signal]:
    signals: list[Signal] = []
    for ticket in tickets:
        if not ticket.is_open or ticket.target is None:
            continue

        target = ticket.target
        if not target.measurable:
            if ticket.severity.is_high:
                signals.append(
                    Signal(
                        kind="needs_manual_check",
                        severity="P3",
                        title=f"{ticket.ticket_id}: target is in business hours",
                        detail=(
                            f"Open {ticket.elapsed} minutes against a target of "
                            f"{target.text}. Whether that is breached depends on a "
                            "working calendar this dataset does not define, so it needs "
                            "a person rather than a guess."
                        ),
                        tickets=(ticket.ticket_id,),
                        accounts=(ticket.account_id,),
                        citations=(target.source,),
                        elapsed_minutes=ticket.elapsed,
                        target=target.text,
                    )
                )
            continue

        assert target.minutes is not None
        if ticket.elapsed > target.minutes:
            over = ticket.elapsed - target.minutes
            signals.append(
                Signal(
                    kind="sla_breached",
                    severity=ticket.severity.level,
                    title=f"{ticket.ticket_id}: first response overdue by {over} minutes",
                    detail=(
                        f"{ticket.severity.level} judged from \"{ticket.severity.because}\". "
                        f"Open {ticket.elapsed} minutes against a {target.text} target."
                    ),
                    tickets=(ticket.ticket_id,),
                    accounts=(ticket.account_id,),
                    citations=(target.source,),
                    elapsed_minutes=ticket.elapsed,
                    target=target.text,
                )
            )
        elif ticket.elapsed >= target.minutes * AT_RISK_FRACTION:
            signals.append(
                Signal(
                    kind="sla_at_risk",
                    severity=ticket.severity.level,
                    title=(
                        f"{ticket.ticket_id}: "
                        f"{target.minutes - ticket.elapsed} minutes left to respond"
                    ),
                    detail=(
                        f"Open {ticket.elapsed} minutes against a {target.text} target."
                    ),
                    tickets=(ticket.ticket_id,),
                    accounts=(ticket.account_id,),
                    citations=(target.source,),
                    elapsed_minutes=ticket.elapsed,
                    target=target.text,
                )
            )
    return signals


# -- one fault, several tickets -------------------------------------------------


def _issue_clusters(tickets: list[_Ticket], chunks: list[Chunk]) -> list[Signal]:
    """Tickets that match the same documented known issue."""
    signals: list[Signal] = []
    for chunk in chunks:
        if chunk.tier is not AuthorityTier.PRODUCT_DOC or not chunk.heading.startswith("KI-"):
            continue

        # Matched on the issue's heading, not its body. The body of a known issue
        # runs to a paragraph of ordinary support vocabulary -- shipment, customer,
        # upload, status -- and any ticket collides with two of those by accident.
        # An outage ticket was being filed under "Bulk Upload failures" on exactly
        # that basis. The heading is the issue's distinctive name, so matching it
        # costs some recall and buys precision, which is the right way round when a
        # false positive reads as "two customers are affected".
        issue_terms = content_terms(chunk.heading)
        matched = [
            ticket
            for ticket in tickets
            if len(issue_terms & content_terms(ticket.text)) >= MATCH_TERMS
        ]
        if len(matched) < 2:
            continue

        accounts = sorted({ticket.account_id for ticket in matched})
        open_count = sum(1 for ticket in matched if ticket.is_open)
        multi = len(accounts) > 1
        signals.append(
            Signal(
                kind="multi_account_issue" if multi else "issue_cluster",
                severity="P2" if multi or open_count > 1 else "P3",
                title=f"{len(matched)} tickets match {chunk.heading}",
                detail=(
                    f"{open_count} still open across "
                    f"{len(accounts)} account(s). "
                    + (
                        "More than one customer is affected, so this is an incident "
                        "rather than a support question."
                        if multi
                        else "A documented known issue is still generating tickets."
                    )
                ),
                tickets=tuple(ticket.ticket_id for ticket in matched),
                accounts=tuple(accounts),
                citations=(chunk.citation,),
            )
        )
    return signals


# -- past answers the current documents contradict ------------------------------


def _unverified_guidance(tickets: list[_Ticket], chunks: list[Chunk]) -> list[Signal]:
    """Closed tickets whose recorded answer may no longer hold.

    The workbook warns outright that some past resolutions are wrong. Where the
    account has since signed an agreement, the risk is concrete rather than
    theoretical: the answer given may have been correct against the general policy
    and wrong for that customer.
    """
    agreements = [
        chunk
        for chunk in chunks
        if chunk.tier is AuthorityTier.AGREEMENT
        and not chunk.is_deprecated
        # The header states who an agreement covers, not what it says. Matching a
        # resolution against "Account: ACCT-002 Customer: LumenWorks Plan: Growth"
        # pairs any mention of a plan name with a contract that may say nothing on
        # the subject -- which is the false positive this whole check is fixing.
        and chunk.heading != HEADER_SECTION_TITLE
    ]

    signals: list[Signal] = []
    for ticket in tickets:
        resolution = str(ticket.row.get("historical_resolution") or "").strip()
        if not resolution:
            continue

        clause = _agreement_on_topic(agreements, ticket.account_id, resolution)
        signals.append(
            Signal(
                kind="unverified_past_answer",
                severity="P3" if clause else "info",
                title=f"{ticket.ticket_id}: past answer not re-checked",
                detail=(
                    f'Recorded resolution: "{resolution}" '
                    + (
                        f"{clause.citation} covers the same ground and may override the "
                        "general policy this answer was based on."
                        if clause
                        else "Historical resolutions are context only and may be wrong."
                    )
                ),
                tickets=(ticket.ticket_id,),
                accounts=(ticket.account_id,),
                citations=(clause.citation,) if clause else (),
            )
        )
    return signals


def _agreement_on_topic(
    agreements: list[Chunk], account_id: str, resolution: str
) -> Chunk | None:
    """A clause of this account's agreement that speaks to what the answer said.

    Merely having an agreement is not enough. LumenWorks has one, but it covers
    support targets, cancellation and pickup credits -- so citing it against a
    ticket about CSV row limits asserts a relationship the corpus does not contain.
    That is this system's own failure mode, produced by the view meant to catch it.
    """
    terms = content_terms(resolution)
    best: tuple[int, Chunk] | None = None
    for chunk in agreements:
        if chunk.scope != account_id:
            continue
        shared = len(terms & content_terms(chunk.text))
        if shared >= MATCH_TERMS and (best is None or shared > best[0]):
            best = (shared, chunk)
    return best[1] if best else None


__all__ = ["AT_RISK_FRACTION", "MATCH_TERMS", "Signal", "detect"]

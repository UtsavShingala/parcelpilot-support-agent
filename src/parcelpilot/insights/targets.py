"""First-response targets, read out of the documents rather than written down here.

The numbers live in the corpus and nowhere else: the current policy carries a table
of plan defaults, and a customer agreement may replace them for its own account. The
same authority rule the agent follows applies here -- an agreement outranks the
general policy -- so an ops view and an answer cannot disagree about what a
customer is owed.

**Business-hours targets are not converted into a deadline.** "4 business hours"
needs a working calendar the corpus never defines: no office hours, no weekends, no
holidays. Assuming eight-hour days would produce a precise breach time built on an
invented calendar, which is the failure this system exists to avoid. Those targets
are surfaced with the elapsed time beside them and marked as needing a person, while
targets stated in plain clock time -- "30 minutes, 24x7" -- are measured exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from parcelpilot.ingest.authority import AuthorityTier
from parcelpilot.ingest.documents import Chunk

SEVERITIES = ("P1", "P2", "P3")

# "- P1: 15 minutes, 24x7" in an agreement.
_AGREEMENT_TARGET = re.compile(r"\bP(?P<level>[123])\s*:\s*(?P<target>[^\n|]+)", re.IGNORECASE)

# "Enterprise 30 minutes, 24x7 2 hours 1 business day" in the policy's plan table.
_PLAN_ROW = re.compile(r"\b(Enterprise|Growth|Standard)\b", re.IGNORECASE)
_DURATION = re.compile(
    r"\d+\s*(?:business\s+)?(?:minute|hour|day)s?(?:\s*,\s*24x7)?", re.IGNORECASE
)

_CLOCK_MINUTES = {"minute": 1, "hour": 60, "day": 1440}


@dataclass(frozen=True)
class Target:
    """A first-response target, and whether it can be turned into a deadline."""

    text: str
    source: str
    """The citation this came from, so a reader can check it."""
    minutes: int | None = None
    """Elapsed-clock minutes, or None when the target is stated in business time."""

    @property
    def measurable(self) -> bool:
        return self.minutes is not None


def parse_duration(text: str) -> int | None:
    """Minutes for a plain clock duration; None for anything on a business calendar."""
    cleaned = text.strip().lower()
    if "business" in cleaned:
        return None  # needs a working calendar the corpus does not define
    match = re.search(r"(\d+)\s*(minute|hour|day)s?", cleaned)
    if not match:
        return None
    return int(match.group(1)) * _CLOCK_MINUTES[match.group(2)]


def targets_for(
    chunks: list[Chunk], *, account_id: str | None, plan: str | None
) -> dict[str, Target]:
    """Resolve P1/P2/P3 targets for one account, agreement first, then plan default."""
    resolved = _plan_defaults(chunks, plan) if plan else {}
    if account_id:
        resolved.update(_agreement_targets(chunks, account_id))
    return resolved


def _agreement_targets(chunks: list[Chunk], account_id: str) -> dict[str, Target]:
    found: dict[str, Target] = {}
    for chunk in chunks:
        if chunk.tier is not AuthorityTier.AGREEMENT or chunk.scope != account_id:
            continue
        if "P1" not in chunk.text:
            continue
        for match in _AGREEMENT_TARGET.finditer(chunk.text):
            level = f"P{match.group('level')}"
            text = match.group("target").strip().rstrip(".")
            found.setdefault(
                level, Target(text=text, source=chunk.citation, minutes=parse_duration(text))
            )
    return found


def _plan_defaults(chunks: list[Chunk], plan: str) -> dict[str, Target]:
    """Pull one plan's row out of the policy's first-response table."""
    for chunk in chunks:
        if chunk.tier is not AuthorityTier.CURRENT_POLICY or chunk.is_deprecated:
            continue
        if "first-response" not in chunk.heading.lower():
            continue

        rows = _split_plan_rows(chunk.text)
        durations = rows.get(plan.strip().lower())
        if not durations:
            continue
        return {
            level: Target(
                text=duration, source=chunk.citation, minutes=parse_duration(duration)
            )
            for level, duration in zip(SEVERITIES, durations, strict=False)
        }
    return {}


def _split_plan_rows(text: str) -> dict[str, list[str]]:
    """Turn the flattened table into one list of durations per plan.

    The PDF's table arrives as a single run of words, so the plan names are the only
    row boundaries available: everything between "Enterprise" and "Growth" belongs to
    Enterprise.
    """
    marks = list(_PLAN_ROW.finditer(text))
    rows: dict[str, list[str]] = {}
    for position, mark in enumerate(marks):
        end = marks[position + 1].start() if position + 1 < len(marks) else len(text)
        segment = text[mark.end() : end]
        rows[mark.group(1).lower()] = [
            found.group(0).strip() for found in _DURATION.finditer(segment)
        ]
    return rows


__all__ = ["SEVERITIES", "Target", "parse_duration", "targets_for"]

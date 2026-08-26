"""How much a document is allowed to influence an answer.

The corpus states its own precedence rule. Support Policy v3, section 1:

    When sources conflict, use the signed customer agreement first, then the
    current support policy, then current product documentation. Historical
    tickets and internal notes are context only and may contain incorrect
    past guidance.

:class:`AuthorityTier` encodes exactly that, and every chunk carries one. Ranking
consults the tier before it consults lexical similarity, so a deprecated policy
cannot win an answer by being worded more like the question.

Metadata is read from the header block each document carries -- ``Status:``,
``Effective:``, ``Superseded by:``, ``Account:`` -- rather than from its filename.
Filenames are a courtesy from whoever exported the pack; the header is part of
the document itself, survives renaming, and is what a human reads to decide
whether a page still applies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import IntEnum

# "Status:", "Superseded by:", "Account:" -- a capitalised word, optionally followed by
# lowercase words, immediately before a colon. Requiring the colon keeps shouty values
# like "DEPRECATED - DO NOT USE" from being mistaken for keys.
_HEADER_KEY = re.compile(r"(?P<key>[A-Z][A-Za-z]*(?: [a-z]+)*)\s*:\s*")

_ACCOUNT_ID = re.compile(r"\b[A-Z]{2,}-\d+\b")
_VERSION = re.compile(r"\bv(\d+)\b", re.IGNORECASE)
_DEPRECATED_MARKERS = ("deprecated", "do not use", "superseded", "retired", "obsolete")

GLOBAL_SCOPE = "global"


class AuthorityTier(IntEnum):
    """Precedence when sources disagree. Lower is more authoritative."""

    AGREEMENT = 1
    """A signed customer agreement. Binds only the account that signed it."""

    CURRENT_POLICY = 2
    """Policy or SOP currently in force; the default for everyone else."""

    PRODUCT_DOC = 3
    """How the product behaves, including known issues. Explains, does not entitle."""

    HISTORICAL = 4
    """Past ticket resolutions. Context only -- the pack warns these can be wrong."""

    DEPRECATED = 5
    """Superseded material. Excluded from answers except to explain what changed."""


@dataclass(frozen=True)
class DocumentAuthority:
    """Everything about a document that decides how far it can be trusted."""

    doc_type: str
    tier: AuthorityTier
    status: str
    scope: str
    version: str | None = None
    effective_date: date | None = None
    term_start: date | None = None
    term_end: date | None = None
    supersedes: str | None = None
    superseded_by: str | None = None

    @property
    def is_deprecated(self) -> bool:
        return self.tier is AuthorityTier.DEPRECATED

    @property
    def is_account_scoped(self) -> bool:
        return self.scope != GLOBAL_SCOPE


def parse_header(header: str) -> dict[str, str]:
    """Split a header block into its ``Key: value`` pairs, keyed in lower case."""
    matches = list(_HEADER_KEY.finditer(header))
    fields: dict[str, str] = {}
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(header)
        value = header[match.end() : end].strip()
        if value:
            fields[match.group("key").strip().lower()] = value
    return fields


def derive_authority(*, title: str, header: str) -> DocumentAuthority:
    """Decide a document's tier and scope from its own header block and title.

    Rules are applied in order, most decisive first:

    1. A document that announces itself as superseded is deprecated, whatever
       else it says. This is the trap in the pack -- v2 reads like a perfectly
       ordinary policy apart from that one line.
    2. A document naming an account is an agreement, and is scoped to it.
    3. A policy or SOP is general and current.
    4. Anything else is product documentation.
    """
    fields = parse_header(header)
    status_text = fields.get("status", "")
    superseded_by = fields.get("superseded by")
    account = fields.get("account")

    doc_type = _classify(title, has_account=account is not None)
    term_start, term_end = _parse_term(fields.get("term"))
    common = {
        "doc_type": doc_type,
        "version": _version(title),
        "effective_date": _parse_date(fields.get("effective") or fields.get("updated")),
        "term_start": term_start,
        "term_end": term_end,
        "supersedes": fields.get("supersedes"),
        "superseded_by": superseded_by,
    }

    if superseded_by or _looks_deprecated(status_text):
        return DocumentAuthority(
            tier=AuthorityTier.DEPRECATED, status="deprecated", scope=GLOBAL_SCOPE, **common
        )

    if account:
        return DocumentAuthority(
            tier=AuthorityTier.AGREEMENT, status="current", scope=_account_id(account), **common
        )

    tier = (
        AuthorityTier.CURRENT_POLICY
        if doc_type in {"policy", "sop"}
        else AuthorityTier.PRODUCT_DOC
    )
    return DocumentAuthority(tier=tier, status="current", scope=GLOBAL_SCOPE, **common)


def _classify(title: str, *, has_account: bool) -> str:
    lowered = title.lower()
    if has_account or "agreement" in lowered:
        return "agreement"
    if "sop" in lowered or "procedure" in lowered:
        return "sop"
    if "policy" in lowered:
        return "policy"
    return "guide"


def _looks_deprecated(status: str) -> bool:
    lowered = status.lower()
    return any(marker in lowered for marker in _DEPRECATED_MARKERS)


def _account_id(value: str) -> str:
    match = _ACCOUNT_ID.search(value)
    return match.group(0) if match else value.strip()


def _version(title: str) -> str | None:
    match = _VERSION.search(title)
    return f"v{match.group(1)}" if match else None


def _parse_date(value: str | None) -> date | None:
    """Read a date like ``15 June 2026`` from the start of a header value."""
    if not value:
        return None
    match = re.match(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", value.strip())
    if not match:
        return None
    try:
        return datetime.strptime(" ".join(match.groups()), "%d %B %Y").date()
    except ValueError:
        return None


def _parse_term(value: str | None) -> tuple[date | None, date | None]:
    """Split a term such as ``1 January 2026 to 31 December 2026`` into its bounds."""
    if not value:
        return None, None
    parts = re.split(r"\s+(?:to|until|through|-)\s+", value, maxsplit=1)
    start = _parse_date(parts[0])
    end = _parse_date(parts[1]) if len(parts) > 1 else None
    return start, end

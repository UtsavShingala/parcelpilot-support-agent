"""Suggesting a severity for a ticket that does not carry one.

The workbook records no severity, but every SLA question depends on one, so an ops
view that refuses to guess shows nothing useful. The compromise is to suggest rather
than assert, and to show the working: each suggestion names the phrase from the
current policy that produced it, so a reader can see the reasoning and overrule it.

The rules are the policy's own definitions, not invented categories. Support Policy
v3 section 2 says P1 is "complete production outage preventing all shipment creation
for a customer, confirmed security incident or suspected credential exposure"; P2 is
"major feature unavailable or materially degraded ... but core operations remain
possible or a workaround exists"; P3 is "minor defect, how-to question, configuration
request". The patterns below are those sentences turned into matches.

Deliberately not a model call. A dashboard that costs money to open does not get
opened, and a severity that changes between refreshes cannot be trusted or tested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_SEVERITY = "P3"


@dataclass(frozen=True)
class SeverityRule:
    severity: str
    pattern: re.Pattern[str]
    because: str
    """The policy language this rule stands on, shown next to the suggestion."""


# Checked before the severity rules. A question about something that failed is still
# a question: "how do I fix the failed login?" matches the P2 wording on "failed",
# and severity chooses which SLA target the ops view measures against, so the
# mis-read silently swaps the deadline too.
_ASKING_HOW = re.compile(
    r"^\s*(how (do|can|would|should|to)\b|what is the (process|procedure)\b|"
    r"where (do|can)\b|can (i|we) (change|update|configure|set)\b)",
    re.IGNORECASE,
)


# Ordered, most severe first: a ticket matching both P1 and P2 language is a P1.
RULES: tuple[SeverityRule, ...] = (
    SeverityRule(
        severity="P1",
        pattern=re.compile(
            r"\b(api key|credential|password|secret|token)s?\b.{0,40}"
            r"\b(expos|leak|publish|share|post)|"
            r"\b(security incident|breach|compromis)",
            re.IGNORECASE | re.DOTALL,
        ),
        because="confirmed security incident or suspected credential exposure",
    ),
    SeverityRule(
        severity="P1",
        pattern=re.compile(
            r"\b(all|every|complete|entire)\b.{0,60}"
            r"\b(fail|down|outage|unavailable|error|500)|"
            r"\b(cannot|can't|unable to)\b.{0,30}\b(create|book)\b.{0,20}\b(any|all)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        because="complete production outage preventing all shipment creation",
    ),
    SeverityRule(
        severity="P2",
        # Stems rather than exact words: "fails", "failing" and "failure" are the
        # same report, and an exact-word list quietly misses most of them.
        pattern=re.compile(
            r"\b(fail\w*|broken|not working|degraded|error\w*|times? out|stuck|"
            r"unable|rejected)\b",
            re.IGNORECASE,
        ),
        because="major feature unavailable or materially degraded, workaround exists",
    ),
    SeverityRule(
        severity="P3",
        pattern=re.compile(
            r"\b(how do|how to|how can|change|update|configure|question|request)\b",
            re.IGNORECASE,
        ),
        because="how-to question or configuration request",
    ),
)


@dataclass(frozen=True)
class Severity:
    """A suggested severity, and the policy language behind it."""

    level: str
    because: str
    confident: bool
    """False when nothing matched and the default was used."""

    @property
    def is_high(self) -> bool:
        return self.level in {"P1", "P2"}


def classify(subject: str, description: str = "") -> Severity:
    """Suggest a severity for a ticket from its own words."""
    text = f"{subject} {description}".strip()

    # A how-to phrased around a failure is still a how-to. Checked before the rules
    # rather than reordered into them, because the P1 patterns must still win: "how
    # do I rotate the API key I leaked?" is a security incident, whatever its
    # grammar.
    if _ASKING_HOW.match(subject.strip()) and not _matches_p1(text):
        return Severity(
            level="P3",
            because="a how-to question, even though it mentions something failing",
            confident=True,
        )

    for rule in RULES:
        if rule.pattern.search(text):
            return Severity(level=rule.severity, because=rule.because, confident=True)
    return Severity(
        level=DEFAULT_SEVERITY,
        because="nothing matched a higher severity; treated as the default",
        confident=False,
    )


def _matches_p1(text: str) -> bool:
    return any(rule.pattern.search(text) for rule in RULES if rule.severity == "P1")


__all__ = ["DEFAULT_SEVERITY", "RULES", "Severity", "SeverityRule", "classify"]

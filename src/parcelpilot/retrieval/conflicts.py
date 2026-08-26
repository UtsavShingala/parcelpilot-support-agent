"""Notice when two retrieved passages answer the same question differently.

Ranking decides which passage leads. It does not, and should not, decide that the
other one has stopped existing. When a customer's agreement and the general SOP
both speak to a cancellation fee, the honest answer names both and says which one
governs -- "your agreement waives the fee that the standard SOP would charge" is a
better answer than either source alone, and it is the one a support agent would
give.

Two passages are treated as speaking to the same question when they matched the
same terms in the query. That is a deliberately shallow test, and it is the right
depth for the job: the retrieval step has already decided both are relevant, so
what remains is whether they are relevant *to the same part* of the question.

The kind of conflict is read from the text where possible. Agreements in this pack
often say outright that they replace something -- "This clause replaces the default
failed-pickup credit amount and timing threshold in the SOP" -- and where they do,
that is reported as an explicit override rather than as bare precedence.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from parcelpilot.ingest.authority import AuthorityTier
from parcelpilot.retrieval.store import RetrievedChunk

# Two shared query terms is enough to call it the same subject; one is usually the
# topic word alone, which every passage in a small corpus shares.
DEFAULT_MINIMUM_SHARED_TERMS = 2

_OVERRIDE_LANGUAGE = re.compile(
    r"\b(replace[sd]?|replacing|instead of|in place of|notwithstanding|"
    r"override[sd]?|supersede[sd]?|waive[sd]?)\b",
    re.IGNORECASE,
)

_TIER_DESCRIPTION = {
    AuthorityTier.AGREEMENT: "a signed customer agreement",
    AuthorityTier.CURRENT_POLICY: "current policy",
    AuthorityTier.PRODUCT_DOC: "product documentation",
    AuthorityTier.HISTORICAL: "a past ticket resolution",
    AuthorityTier.DEPRECATED: "a superseded document",
}


class ConflictKind(Enum):
    """Why one passage governs over another."""

    OVERRIDE = "override"
    """The governing passage says in so many words that it replaces the other."""

    PRECEDENCE = "precedence"
    """No explicit override; the authority hierarchy settles it."""

    SUPERSEDED = "superseded"
    """The other passage is from a document that has been replaced outright."""


@dataclass(frozen=True)
class Conflict:
    """Two passages that speak to the same question, and which one governs."""

    kind: ConflictKind
    governing: RetrievedChunk
    subordinate: RetrievedChunk
    shared_terms: tuple[str, ...]

    @property
    def explanation(self) -> str:
        governing, subordinate = self.governing.chunk, self.subordinate.chunk
        if self.kind is ConflictKind.SUPERSEDED:
            return (
                f"{subordinate.citation} has been superseded by {governing.citation} "
                f"and is retained only to explain what changed."
            )
        if self.kind is ConflictKind.OVERRIDE:
            return (
                f"{governing.citation} states that it replaces the general terms in "
                f"{subordinate.citation}."
            )
        return (
            f"{governing.citation} is {_TIER_DESCRIPTION[governing.tier]} and takes "
            f"precedence over {subordinate.citation}, which is "
            f"{_TIER_DESCRIPTION[subordinate.tier]}."
        )


def detect_conflicts(
    hits: Sequence[RetrievedChunk],
    *,
    minimum_shared_terms: int = DEFAULT_MINIMUM_SHARED_TERMS,
) -> list[Conflict]:
    """Report pairs of retrieved passages that address the same point at different tiers.

    At most one conflict is reported per pair of documents -- the best-scoring one --
    so a five-section agreement against a five-section SOP yields one finding rather
    than twenty-five.
    """
    best: dict[tuple[str, str], Conflict] = {}

    for governing, subordinate in _ordered_pairs(hits):
        shared = tuple(sorted(set(governing.matched_terms) & set(subordinate.matched_terms)))
        if len(shared) < minimum_shared_terms:
            continue

        conflict = Conflict(
            kind=_classify(governing, subordinate),
            governing=governing,
            subordinate=subordinate,
            shared_terms=shared,
        )
        key = (governing.chunk.source_file, subordinate.chunk.source_file)
        incumbent = best.get(key)
        if incumbent is None or conflict.governing.score > incumbent.governing.score:
            best[key] = conflict

    return sorted(best.values(), key=lambda found: -found.governing.score)


def _ordered_pairs(
    hits: Sequence[RetrievedChunk],
) -> list[tuple[RetrievedChunk, RetrievedChunk]]:
    """Every pair from different documents, more authoritative passage first."""
    pairs = []
    for first in hits:
        for second in hits:
            if first.chunk.source_file == second.chunk.source_file:
                continue
            if first.chunk.tier < second.chunk.tier:
                pairs.append((first, second))
    return pairs


def _classify(governing: RetrievedChunk, subordinate: RetrievedChunk) -> ConflictKind:
    """Name the relationship, claiming an explicit override only where one exists.

    Override language is only meaningful from an agreement. A current SOP describing
    what it "replaces" is describing its own history, not asserting authority over
    the product documentation it happens to have been retrieved alongside.
    """
    if subordinate.chunk.is_deprecated:
        return ConflictKind.SUPERSEDED
    if governing.chunk.tier is AuthorityTier.AGREEMENT and _OVERRIDE_LANGUAGE.search(
        governing.chunk.text
    ):
        return ConflictKind.OVERRIDE
    return ConflictKind.PRECEDENCE

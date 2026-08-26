"""Search the corpus, weighted by how far each source may be trusted.

Three rules shape every result set.

**A caller only ever sees material they are entitled to.** Account scoping is
applied before scoring, so another customer's agreement cannot be ranked, quoted
or counted -- see :mod:`parcelpilot.retrieval.scope`.

**Superseded material is excluded, not merely demoted.** A deprecated policy is
often worded more like the question than its replacement -- the pack's v2 policy
is a near-copy of v3 with different numbers -- so any amount of down-weighting
leaves it able to win. Callers that genuinely need it, to explain what changed,
ask for it explicitly.

**Authority scales relevance rather than substituting for it.** The tier weight is
a multiplier, so a passage that barely matches the question cannot be promoted
into the answer by the seniority of the document it came from. Among passages that
do match, the more authoritative one wins. Where an agreement and a general policy
both match, that is a conflict to be surfaced rather than silently resolved by
ranking, which is what :mod:`parcelpilot.retrieval.conflicts` is for.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from parcelpilot.config import Settings
from parcelpilot.ingest.authority import AuthorityTier
from parcelpilot.ingest.build_index import load_chunks
from parcelpilot.ingest.documents import Chunk
from parcelpilot.retrieval.lexical import LexicalIndex
from parcelpilot.retrieval.scope import AccountScope
from parcelpilot.retrieval.text import content_terms

DEFAULT_LIMIT = 6

# Multipliers on a 0-1 relevance score. The gaps are deliberately modest: they
# decide ties between comparably relevant passages, not whether something is
# relevant. Historical material sits below 1.0 because the workbook says outright
# that past resolutions may be wrong.
AUTHORITY_MULTIPLIER: dict[AuthorityTier, float] = {
    AuthorityTier.AGREEMENT: 1.30,
    AuthorityTier.CURRENT_POLICY: 1.20,
    AuthorityTier.PRODUCT_DOC: 1.05,
    AuthorityTier.HISTORICAL: 0.80,
    AuthorityTier.DEPRECATED: 0.50,
}


@dataclass(frozen=True)
class RetrievedChunk:
    """A hit, with enough detail to explain why it ranked where it did."""

    chunk: Chunk
    lexical_score: float
    matched_terms: tuple[str, ...]

    @property
    def authority_multiplier(self) -> float:
        return AUTHORITY_MULTIPLIER.get(self.chunk.tier, 1.0)

    @property
    def score(self) -> float:
        return self.lexical_score * self.authority_multiplier

    @property
    def citation(self) -> str:
        return self.chunk.citation


class DocumentStore:
    """An authority-aware view over the ingested chunks."""

    def __init__(self, chunks: Sequence[Chunk]) -> None:
        self._chunks = list(chunks)
        self._index = LexicalIndex([chunk.search_text for chunk in self._chunks])
        self._by_id = {chunk.chunk_id: chunk for chunk in self._chunks}

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> DocumentStore:
        return cls(load_chunks(settings))

    def __len__(self) -> int:
        return len(self._chunks)

    @property
    def chunks(self) -> list[Chunk]:
        return list(self._chunks)

    def get(self, chunk_id: str) -> Chunk | None:
        return self._by_id.get(chunk_id)

    def search(
        self,
        query: str,
        *,
        scope: AccountScope | None = None,
        limit: int = DEFAULT_LIMIT,
        include_deprecated: bool = False,
    ) -> list[RetrievedChunk]:
        """Return the best matches for ``query``, most authoritative first among equals.

        Passages the caller may not see are dropped before scoring, so another
        account's agreement cannot be quoted, ranked or counted. Passages that match
        nothing in the query are dropped before authority is applied, so seniority
        can never manufacture relevance.

        ``scope`` defaults to :meth:`AccountScope.none`: forgetting to pass a caller
        context yields general material only, never everything.
        """
        visible = scope or AccountScope.none()
        scores = self._index.scores(query)
        terms = content_terms(query)

        hits = [
            RetrievedChunk(
                chunk=chunk,
                lexical_score=score,
                matched_terms=tuple(sorted(terms & content_terms(chunk.search_text))),
            )
            for chunk, score in zip(self._chunks, scores, strict=True)
            if visible.permits(chunk.scope)
            and score > 0.0
            and (include_deprecated or not chunk.is_deprecated)
        ]
        hits.sort(key=lambda hit: (-hit.score, hit.chunk.chunk_id))
        return hits[:limit]

    def visible_chunks(self, scope: AccountScope | None = None) -> list[Chunk]:
        """Every chunk ``scope`` may read. Useful for auditing what a caller can reach."""
        visible = scope or AccountScope.none()
        return [chunk for chunk in self._chunks if visible.permits(chunk.scope)]

    def superseded_by_tier(self, tier: AuthorityTier) -> list[Chunk]:
        """Every chunk at ``tier``. Used to explain what a current document replaced."""
        return [chunk for chunk in self._chunks if chunk.tier is tier]


def rank(hits: Iterable[RetrievedChunk]) -> list[RetrievedChunk]:
    """Sort hits the way :meth:`DocumentStore.search` does."""
    return sorted(hits, key=lambda hit: (-hit.score, hit.chunk.chunk_id))

"""BM25 scoring over the chunk store.

The corpus is a couple of dozen short passages. That is far too small for a vector
database to earn its keep, and small enough that lexical matching over headings and
body text finds the right section reliably. Embeddings remain worth adding for
paraphrased questions; the score returned here is normalised to 0-1 so a semantic
score can be blended in later without re-tuning anything above it.

BM25+ rather than the more familiar BM25-Okapi, because of that same small size.
Okapi's IDF is ``log(N - n + 0.5) - log(n + 0.5)``, which reaches zero for a term
appearing in half the corpus and goes negative beyond that. Across 25 chunks that
threshold lands on ordinary topic words: "cancellation" occurs in enough passages
to be scored as noise. BM25+ keeps IDF strictly positive, so a common word is
merely weak evidence rather than none at all.
"""

from __future__ import annotations

from collections.abc import Sequence

from rank_bm25 import BM25Plus

from parcelpilot.retrieval.text import tokenize


class LexicalIndex:
    """BM25 over a fixed set of documents, addressed by position."""

    def __init__(self, documents: Sequence[str]) -> None:
        self._tokenised = [tokenize(document) for document in documents]
        # BM25 divides by the corpus average length and cannot be built from
        # nothing; an empty index scores everything zero instead of raising.
        self._bm25 = BM25Plus(self._tokenised) if any(self._tokenised) else None

    def __len__(self) -> int:
        return len(self._tokenised)

    def scores(self, query: str) -> list[float]:
        """Score every document against ``query``, normalised so the best match is 1.0.

        Normalising per query keeps the scale stable across questions of different
        lengths, which is what lets a fixed authority weight mean the same thing
        for every query.
        """
        terms = tokenize(query)
        if self._bm25 is None or not terms:
            return [0.0] * len(self._tokenised)

        raw = [float(score) for score in self._bm25.get_scores(terms)]
        best = max(raw, default=0.0)
        if best <= 0:
            return [0.0] * len(self._tokenised)
        return [max(score, 0.0) / best for score in raw]

"""BM25 scoring over the chunk store.

The corpus is a couple of dozen short passages. That is far too small for a vector
database to earn its keep, and small enough that lexical matching over headings and
body text finds the right section reliably. Embeddings remain worth adding for
paraphrased questions; the score returned here is normalised to 0-1 so a semantic
score can be blended in later without re-tuning anything above it.

The ranking function is BM25 with Lucene's IDF,
``ln(1 + (N - n + 0.5) / (n + 0.5))``, chosen over the two obvious off-the-shelf
options for reasons that only show up at this corpus size:

* **BM25-Okapi** uses ``log(N - n + 0.5) - log(n + 0.5)``, which reaches zero for a
  term appearing in half the corpus and goes negative past that. Over 25 chunks
  that threshold lands on ordinary topic words -- "cancellation" occurs often
  enough to be scored as noise.
* **BM25+** fixes the negative IDF by adding a constant to every term's
  contribution, including for documents that do not contain the term. Every
  passage then scores above zero for any recognised query, so "did this passage
  match at all?" stops being answerable -- and that question is what stops an
  authoritative document being promoted into an answer it has nothing to say about.

Lucene's IDF is positive for every term while leaving a passage that shares no
terms with the query at exactly zero, which is both properties at once.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence

from parcelpilot.retrieval.text import tokenize

# Standard BM25 parameters: k1 controls how fast term frequency saturates, b how
# strongly long documents are penalised. These are the usual defaults, and the
# corpus is far too small to justify tuning them against it.
K1 = 1.5
B = 0.75


class LexicalIndex:
    """BM25 over a fixed set of documents, addressed by position."""

    def __init__(self, documents: Sequence[str]) -> None:
        self._documents = [Counter(tokenize(document)) for document in documents]
        self._lengths = [sum(counts.values()) for counts in self._documents]
        total = sum(self._lengths)
        self._average_length = total / len(self._lengths) if self._lengths else 0.0
        self._idf = self._compute_idf()

    def __len__(self) -> int:
        return len(self._documents)

    def _compute_idf(self) -> dict[str, float]:
        count = len(self._documents)
        appearances: Counter[str] = Counter()
        for counts in self._documents:
            appearances.update(counts.keys())
        return {
            term: math.log(1 + (count - seen + 0.5) / (seen + 0.5))
            for term, seen in appearances.items()
        }

    def scores(self, query: str) -> list[float]:
        """Score every document against ``query``, normalised so the best match is 1.0.

        A document sharing no terms with the query scores exactly zero. Normalising
        per query keeps the scale stable across questions of different lengths,
        which is what lets a fixed authority weight mean the same thing every time.
        """
        terms = [term for term in tokenize(query) if term in self._idf]
        if not terms or not self._average_length:
            return [0.0] * len(self._documents)

        raw = [self._score_document(index, terms) for index in range(len(self._documents))]
        best = max(raw, default=0.0)
        if best <= 0:
            return [0.0] * len(self._documents)
        return [score / best for score in raw]

    def _score_document(self, index: int, terms: Sequence[str]) -> float:
        counts = self._documents[index]
        length = self._lengths[index]
        normaliser = K1 * (1 - B + B * length / self._average_length)

        total = 0.0
        for term in terms:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            total += self._idf[term] * frequency * (K1 + 1) / (frequency + normaliser)
        return total

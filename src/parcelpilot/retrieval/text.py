"""Tokenisation for a corpus full of identifiers.

Support questions are dense with codes -- ``ORD-1001``, ``ACCT-002``, ``KI-208``,
``P1`` -- and a tokeniser that splits on every non-letter would turn ``KI-208``
into ``ki`` and ``208``, so a question about KI-208 would match every passage that
happens to mention 208. Splitting on nothing is no better: someone who writes
"issue 208" would then match nothing at all.

Hyphenated tokens are therefore emitted whole *and* in parts. The whole form
carries the precision, the parts carry the recall, and BM25 weighs them by how
rare they turn out to be.
"""

from __future__ import annotations

import re

# Thousands separators inside numbers only: "INR 1,000" is one number, but
# "P1, P2" is two tokens and must stay that way.
_THOUSANDS = re.compile(r"(?<=\d),(?=\d)")
_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

# Only for judging whether two passages discuss the same thing. BM25 needs no
# stopword list -- it already discounts words that appear everywhere.
STOPWORDS = frozenset(
    """
    a an and any are as at be been but by can do does for from had has have how i if in
    into is it its may must not of on or should so than that the their then there these
    this to under until up was we were what when where which who why will with within
    would you your
    """.split()
)


def tokenize(text: str) -> list[str]:
    """Break text into index terms, keeping identifiers addressable both ways."""
    normalised = _THOUSANDS.sub("", text.lower())
    tokens: list[str] = []
    for match in _TOKEN.finditer(normalised):
        token = match.group(0)
        tokens.append(token)
        if "-" in token:
            tokens.extend(part for part in token.split("-") if part)
    return tokens


def content_terms(text: str) -> set[str]:
    """The meaningful terms in ``text``, for comparing what two passages are about."""
    return {token for token in tokenize(text) if token not in STOPWORDS and len(token) > 1}

"""Binding a number to the document it came from.

The calculator takes policy figures as arguments -- a free-cancellation window, a
credit cap, a response target -- because the alternative is a table of thresholds
in code that goes stale the moment a document changes. That is the right call, and
it left a hole: nothing checked the model had actually read those numbers anywhere.

A hallucinated figure was indistinguishable from a correct one. The arithmetic ran
faithfully on it, the answer came back precise and well-cited, and the citation was
real even when the number was not. That is the exact failure this system exists to
prevent, produced by the part of it meant to prevent guessing.

So every figure now names the passage it came from, and that passage is fetched
through the caller's own scope and searched for the value. It cannot be grounded in
a document the caller may not see, in a superseded one, or in one that does not
contain the number. What it does not catch is a right-number-wrong-clause misread:
the SOP's 30-minute window is genuinely in the SOP, so quoting it at a customer
whose agreement overrides it still grounds. Conflict detection is what surfaces
that, and the answer must still name both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from parcelpilot.ingest.documents import Chunk
from parcelpilot.retrieval.scope import AccountScope
from parcelpilot.retrieval.store import DocumentStore

# "INR 5,000" and "5000" are the same figure; the corpus writes it both ways.
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")


class NotGrounded(ValueError):
    """A figure could not be found in any passage the caller cited."""


@dataclass(frozen=True)
class Grounding:
    """The passages a calculation's figures were checked against."""

    chunks: tuple[Chunk, ...]

    @property
    def text(self) -> str:
        return _normalise(" ".join(chunk.text for chunk in self.chunks))

    @property
    def citations(self) -> tuple[str, ...]:
        return tuple(chunk.citation for chunk in self.chunks)

    def require(self, **figures: float | None) -> None:
        """Refuse unless every supplied figure appears in one of these passages."""
        for name, value in figures.items():
            if value is None:
                continue
            if not _appears(float(value), self.text):
                raise NotGrounded(
                    f"{name}={_render(float(value))} does not appear in the passages you "
                    f"cited ({', '.join(self.citations) or 'none'}). Quote the figure from "
                    "the governing document, or search for it first -- do not supply a "
                    "number you have not read."
                )


def resolve(store: DocumentStore, scope: AccountScope, sources: list[str]) -> Grounding:
    """Fetch the cited passages, refusing any the caller may not read.

    Accepts a chunk id or the citation text, because a model reliably echoes the
    citation it was shown and less reliably echoes an opaque id.
    """
    if not sources:
        raise NotGrounded(
            "no sources were cited. Every figure in a calculation must come from a "
            "document: search first, then pass the citation you read it in."
        )

    by_citation = {chunk.citation: chunk for chunk in store.chunks}
    found: list[Chunk] = []
    for source in sources:
        chunk = store.get(source) or by_citation.get(source) or _by_prefix(by_citation, source)
        if chunk is None:
            raise NotGrounded(
                f"no passage matches {source!r}. Cite a source exactly as search_documents "
                "returned it."
            )
        if not scope.permits(chunk.scope):
            # Refused rather than ignored: silently dropping it would let a figure be
            # grounded in a document this caller was never allowed to read.
            raise NotGrounded(f"{chunk.citation} is not available to this user.")
        if chunk.is_deprecated:
            raise NotGrounded(
                f"{chunk.citation} has been superseded and cannot support a calculation. "
                "Use the current policy or the customer's agreement."
            )
        found.append(chunk)
    return Grounding(chunks=tuple(found))


def _by_prefix(by_citation: dict[str, Chunk], source: str) -> Chunk | None:
    """Tolerate a citation that was truncated or lightly reworded on the way back."""
    trimmed = source.strip().rstrip(".")
    for citation, chunk in by_citation.items():
        if citation.startswith(trimmed) or trimmed.startswith(citation):
            return chunk
    return None


def _appears(value: float, text: str) -> bool:
    """Whether ``value`` occurs in ``text`` as a figure rather than inside another."""
    return any(
        re.search(rf"(?<![\d.]){re.escape(form)}(?![\d])", text)
        for form in _forms(value)
    )


def _forms(value: float) -> list[str]:
    """The ways the corpus might write this number."""
    forms = [_render(value)]
    if value.is_integer():
        forms.append(f"{int(value)}.0")
    return forms


def _render(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _normalise(text: str) -> str:
    return _THOUSANDS.sub("", text)


__all__ = ["Grounding", "NotGrounded", "resolve"]

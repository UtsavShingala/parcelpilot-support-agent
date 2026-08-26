"""Assemble retrievable chunks from parsed documents.

A chunk is one section of one document, carrying the authority of the document it
came from. Sections are already the unit a human would cite -- "the SOP, section
2" -- so they are kept whole rather than cut to a fixed token window. In this pack
the longest runs to a few hundred characters.

The header block becomes a chunk too. It is where a document states its status,
version and the account it applies to, which is what answers questions about which
policy is in force or what a customer's plan is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from parcelpilot.ingest.authority import AuthorityTier, DocumentAuthority, derive_authority
from parcelpilot.ingest.sections import ParsedDocument, parse_pdf

HEADER_SECTION_TITLE = "Document header"

# Sections in this pack are short. The cap only matters for corpora whose sections
# run long enough that a single chunk would be too coarse to retrieve usefully.
DEFAULT_MAX_CHARS = 1500

_SLUG_NOISE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Chunk:
    """One citable passage, and how far it may be trusted."""

    chunk_id: str
    text: str
    source_file: str
    doc_title: str
    heading_path: tuple[str, ...]
    section_number: str | None
    page: int
    authority: DocumentAuthority

    @property
    def tier(self) -> AuthorityTier:
        return self.authority.tier

    @property
    def scope(self) -> str:
        return self.authority.scope

    @property
    def is_deprecated(self) -> bool:
        return self.authority.is_deprecated

    @property
    def heading(self) -> str:
        return self.heading_path[-1] if self.heading_path else ""

    @property
    def citation(self) -> str:
        """How this passage should be referred to in an answer."""
        return f"{self.doc_title} - {self.heading}" if self.heading else self.doc_title

    @property
    def search_text(self) -> str:
        """Text offered to the index, headings included.

        Headings carry the words a question is most likely to use -- "cancellation",
        "service credits" -- while the body often refers to them only obliquely.
        """
        return "\n".join([*self.heading_path, self.text])

    def to_dict(self) -> dict[str, Any]:
        authority = self.authority
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "source_file": self.source_file,
            "doc_title": self.doc_title,
            "heading_path": list(self.heading_path),
            "section_number": self.section_number,
            "page": self.page,
            "authority": {
                "doc_type": authority.doc_type,
                "tier": authority.tier.name,
                "status": authority.status,
                "scope": authority.scope,
                "version": authority.version,
                "effective_date": _iso(authority.effective_date),
                "term_start": _iso(authority.term_start),
                "term_end": _iso(authority.term_end),
                "supersedes": authority.supersedes,
                "superseded_by": authority.superseded_by,
            },
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Chunk:
        authority = payload["authority"]
        return cls(
            chunk_id=payload["chunk_id"],
            text=payload["text"],
            source_file=payload["source_file"],
            doc_title=payload["doc_title"],
            heading_path=tuple(payload["heading_path"]),
            section_number=payload["section_number"],
            page=payload["page"],
            authority=DocumentAuthority(
                doc_type=authority["doc_type"],
                tier=AuthorityTier[authority["tier"]],
                status=authority["status"],
                scope=authority["scope"],
                version=authority["version"],
                effective_date=_from_iso(authority["effective_date"]),
                term_start=_from_iso(authority["term_start"]),
                term_end=_from_iso(authority["term_end"]),
                supersedes=authority["supersedes"],
                superseded_by=authority["superseded_by"],
            ),
        )


def load_corpus(corpus_dir: Path, *, max_chars: int = DEFAULT_MAX_CHARS) -> list[Chunk]:
    """Parse every PDF in ``corpus_dir`` into authority-tagged chunks."""
    chunks: list[Chunk] = []
    for path in sorted(corpus_dir.glob("*.pdf")):
        chunks.extend(chunks_from_document(parse_pdf(path), max_chars=max_chars))
    return chunks


def chunks_from_document(
    document: ParsedDocument, *, max_chars: int = DEFAULT_MAX_CHARS
) -> list[Chunk]:
    """Turn one parsed document into chunks that all share its authority."""
    authority = derive_authority(title=document.title, header=document.header)
    stem = Path(document.source_file).stem
    taken: set[str] = set()
    chunks: list[Chunk] = []

    passages: list[tuple[str, tuple[str, ...], str | None, int]] = []
    if document.header:
        passages.append((document.header, (HEADER_SECTION_TITLE,), None, 1))
    passages.extend(
        (section.text, section.heading_path, section.number, section.page)
        for section in document.sections
    )

    for text, heading_path, section_number, page in passages:
        label = section_number or _slug(heading_path[-1] if heading_path else "section")
        parts = _split(text, max_chars)
        for index, part in enumerate(parts, start=1):
            suffix = "" if len(parts) == 1 else f"-p{index}"
            chunks.append(
                Chunk(
                    chunk_id=_unique(f"{stem}#{label}{suffix}", taken),
                    text=part,
                    source_file=document.source_file,
                    doc_title=document.title,
                    heading_path=heading_path,
                    section_number=section_number,
                    page=page,
                    authority=authority,
                )
            )
    return chunks


def _split(text: str, max_chars: int) -> list[str]:
    """Break over-long text on line boundaries, keeping bullets intact.

    A single line longer than the cap is left whole rather than cut mid-sentence.
    """
    if len(text) <= max_chars:
        return [text]

    parts: list[str] = []
    current: list[str] = []
    length = 0
    for line in text.split("\n"):
        if current and length + len(line) + 1 > max_chars:
            parts.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        parts.append("\n".join(current))
    return parts


def _unique(candidate: str, taken: set[str]) -> str:
    chunk_id = candidate
    attempt = 2
    while chunk_id in taken:
        chunk_id = f"{candidate}-{attempt}"
        attempt += 1
    taken.add(chunk_id)
    return chunk_id


def _slug(value: str) -> str:
    return _SLUG_NOISE.sub("-", value.lower()).strip("-")[:40] or "section"


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _from_iso(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None

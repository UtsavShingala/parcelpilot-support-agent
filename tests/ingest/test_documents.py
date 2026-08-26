"""Chunks must be uniquely addressable, citable, and carry their document's authority."""

from __future__ import annotations

from pathlib import Path

from parcelpilot.ingest.authority import GLOBAL_SCOPE, AuthorityTier
from parcelpilot.ingest.documents import (
    HEADER_SECTION_TITLE,
    Chunk,
    chunks_from_document,
    load_corpus,
)
from parcelpilot.ingest.sections import ParsedDocument, Section


def test_the_corpus_loads_into_non_empty_chunks(corpus_dir: Path) -> None:
    chunks = load_corpus(corpus_dir)
    assert chunks
    assert all(chunk.text.strip() for chunk in chunks)
    assert all(chunk.doc_title for chunk in chunks)


def test_chunk_ids_are_unique(corpus_dir: Path) -> None:
    chunks = load_corpus(corpus_dir)
    ids = [chunk.chunk_id for chunk in chunks]
    assert len(ids) == len(set(ids))


def test_chunks_survive_a_serialisation_roundtrip(corpus_dir: Path) -> None:
    chunks = load_corpus(corpus_dir)
    assert [Chunk.from_dict(chunk.to_dict()) for chunk in chunks] == chunks


def test_only_agreements_are_account_scoped(corpus_dir: Path) -> None:
    for chunk in load_corpus(corpus_dir):
        if chunk.tier is AuthorityTier.AGREEMENT:
            assert chunk.scope != GLOBAL_SCOPE, f"{chunk.chunk_id} is an unscoped agreement"
        else:
            assert chunk.scope == GLOBAL_SCOPE, f"{chunk.chunk_id} is scoped to one account"


def test_every_document_contributes_its_header(corpus_dir: Path) -> None:
    """The header states status and scope, so questions about it must be answerable."""
    chunks = load_corpus(corpus_dir)
    with_headers = {
        chunk.source_file for chunk in chunks if chunk.heading == HEADER_SECTION_TITLE
    }
    assert with_headers == {chunk.source_file for chunk in chunks}


def _document(*sections: Section, header: str = "Status: CURRENT") -> ParsedDocument:
    return ParsedDocument(
        source_file="handbook.pdf", title="Handbook v1", header=header, sections=sections
    )


def _section(title: str, text: str, number: str | None = None) -> Section:
    return Section(
        number=number, title=title, text=text, heading_path=(title,), level=0, page=1
    )


def test_headings_are_searchable_alongside_the_body() -> None:
    section = _section("2. Failed-pickup service credits", "The customer is eligible when...")
    chunk = chunks_from_document(_document(section))[-1]

    assert "Failed-pickup service credits" in chunk.search_text
    assert "The customer is eligible when" in chunk.search_text
    assert chunk.text == "The customer is eligible when..."


def test_citation_names_the_document_and_the_section() -> None:
    chunk = chunks_from_document(_document(_section("1. Order cancellation", "body")))[-1]
    assert chunk.citation == "Handbook v1 - 1. Order cancellation"


def test_over_long_sections_split_on_line_boundaries() -> None:
    lines = "\n".join(f"- clause {index}" for index in range(60))
    section = _section("1. Clauses", lines, number="1")
    chunks = chunks_from_document(_document(section), max_chars=200)
    body = [chunk for chunk in chunks if chunk.heading != HEADER_SECTION_TITLE]

    assert len(body) > 1
    assert all(len(chunk.text) <= 200 for chunk in body)
    assert [chunk.chunk_id for chunk in body] == [
        f"handbook#1-p{index}" for index in range(1, len(body) + 1)
    ]
    assert "\n".join(chunk.text for chunk in body) == lines


def test_short_sections_are_kept_whole() -> None:
    chunks = chunks_from_document(_document(_section("1. Short", "one line", number="1")))
    body = [chunk for chunk in chunks if chunk.heading != HEADER_SECTION_TITLE]
    assert len(body) == 1
    assert body[0].chunk_id == "handbook#1"


def test_repeated_headings_still_get_distinct_ids() -> None:
    chunks = chunks_from_document(
        _document(_section("Notes", "first"), _section("Notes", "second"))
    )
    ids = [chunk.chunk_id for chunk in chunks]
    assert len(ids) == len(set(ids))


def test_a_document_without_a_header_yields_only_sections() -> None:
    chunks = chunks_from_document(_document(_section("1. Only", "body"), header=""))
    assert [chunk.heading for chunk in chunks] == ["1. Only"]

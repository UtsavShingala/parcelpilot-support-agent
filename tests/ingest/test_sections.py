"""The outline rebuilt from font sizes must be structurally sound."""

from __future__ import annotations

from pathlib import Path

from parcelpilot.ingest.pdf_text import TextRun
from parcelpilot.ingest.sections import parse_pdf, parse_runs


def test_every_document_has_a_title_and_sections(pdf_paths: list[Path]) -> None:
    for path in pdf_paths:
        document = parse_pdf(path)
        assert document.title, f"{path.name} has no title"
        assert document.sections, f"{path.name} produced no sections"


def test_heading_path_depth_matches_nesting_level(pdf_paths: list[Path]) -> None:
    """Guards the sibling-nesting bug: top-level sections must not nest under each other."""
    for path in pdf_paths:
        for section in parse_pdf(path).sections:
            assert len(section.heading_path) == section.level + 1, (
                f"{path.name}: {section.heading_path} is not at depth {section.level}"
            )


def test_sections_are_never_empty(pdf_paths: list[Path]) -> None:
    """A heading that only holds sub-headings is a path component, not a chunk."""
    for path in pdf_paths:
        for section in parse_pdf(path).sections:
            assert section.text.strip(), f"{path.name}: empty section {section.title!r}"


def test_the_title_is_not_also_a_section(pdf_paths: list[Path]) -> None:
    for path in pdf_paths:
        document = parse_pdf(path)
        assert all(section.heading_path[0] != document.title for section in document.sections)


def _run(text: str, size: float) -> TextRun:
    return TextRun(text=text, size=size, page=1)


def test_siblings_stay_siblings_and_children_nest() -> None:
    document = parse_runs(
        [
            _run("Handbook", 30.0),
            _run("Status: CURRENT", 10.0),
            _run("1. First", 20.0),
            _run("body one", 10.0),
            _run("2. Second", 20.0),
            _run("body two", 10.0),
            _run("2a. Child", 15.0),
            _run("body three", 10.0),
        ],
        source_file="handbook.pdf",
    )

    assert document.title == "Handbook"
    assert document.header == "Status: CURRENT"
    paths = [section.heading_path for section in document.sections]
    assert paths == [("1. First",), ("2. Second",), ("2. Second", "2a. Child")]


def test_numbering_is_split_out_when_present() -> None:
    """Body runs are deliberately realistic: body size is the mode by character count."""
    document = parse_runs(
        [
            _run("Handbook", 30.0),
            _run("1. Order cancellation", 20.0),
            _run("A booked shipment may be cancelled before pickup.", 10.0),
            _run("KI-208 - Upload failures", 20.0),
            _run("Large uploads fail intermittently above 3,000 rows.", 10.0),
        ],
        source_file="handbook.pdf",
    )

    numbered, unnumbered = document.sections
    assert (numbered.number, numbered.title) == ("1", "Order cancellation")
    assert (unnumbered.number, unnumbered.title) == (None, "KI-208 - Upload failures")


def test_an_empty_document_parses_without_error() -> None:
    document = parse_runs([], source_file="empty.pdf")
    assert document.title == ""
    assert document.sections == ()

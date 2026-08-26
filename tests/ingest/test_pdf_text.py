"""The extractor must recover a usable outline from Google Docs exports."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from parcelpilot.ingest.pdf_text import extract_runs


def test_every_document_yields_runs(pdf_paths: list[Path]) -> None:
    for path in pdf_paths:
        assert extract_runs(path), f"{path.name} produced no text runs"


def test_headings_are_set_larger_than_body_text(pdf_paths: list[Path]) -> None:
    """Font size is the only heading signal left after export, so it must vary."""
    for path in pdf_paths:
        runs = extract_runs(path)
        sizes = Counter(run.size for run in runs)
        body_size = sizes.most_common(1)[0][0]
        assert any(size > body_size for size in sizes), (
            f"{path.name} has a single font size; the outline cannot be recovered"
        )


def test_bullet_glyphs_become_list_markers(pdf_paths: list[Path]) -> None:
    for path in pdf_paths:
        for run in extract_runs(path):
            assert not set(run.text) & set("●○▪•■◦"), f"raw bullet glyph left in {path.name}"


def test_export_whitespace_is_collapsed(pdf_paths: list[Path]) -> None:
    """The export puts each word on its own line; runs must read as prose."""
    for path in pdf_paths:
        for run in extract_runs(path):
            assert "  " not in run.text
            assert not run.text.startswith(" ")

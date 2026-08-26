"""PDF text extraction that preserves font size.

The source documents are Google Docs exports. Their text layer emits one word per
fragment with no usable line structure, so paragraph and heading boundaries cannot
be recovered from whitespace alone. Font size does survive the export, and headings
are consistently set larger than body text -- so size is what this module keeps.
The section splitter rebuilds the document outline from it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path

from pypdf import PdfReader

# Glyphs Google Docs uses for list markers, at body size and indistinguishable from
# body text once size is the only signal left.
_BULLET_CHARS = "●○▪•■◦"
_BULLET_RUN = re.compile(rf"\s*[{_BULLET_CHARS}]\s*")


@dataclass(frozen=True)
class TextRun:
    """A stretch of text rendered at a single font size."""

    text: str
    size: float
    page: int


def extract_runs(path: Path) -> list[TextRun]:
    """Read ``path`` into runs of text grouped by the size they were set in."""
    reader = PdfReader(str(path))
    runs: list[TextRun] = []
    for page_number, page in enumerate(reader.pages, start=1):
        fragments: list[tuple[float, str]] = []

        def collect(text, cm, tm, font_dict, font_size, sink=fragments):  # noqa: ANN001
            if font_size is None:
                return
            stripped = text.strip()
            if stripped:
                sink.append((round(float(font_size), 1), stripped))

        page.extract_text(visitor_text=collect)
        runs.extend(_merge_adjacent(fragments, page_number))
    return runs


def _merge_adjacent(fragments: list[tuple[float, str]], page: int) -> list[TextRun]:
    """Join neighbouring fragments that share a font size into one run."""
    merged: list[TextRun] = []
    for size, group in groupby(fragments, key=lambda fragment: fragment[0]):
        text = _normalise(" ".join(text for _, text in group))
        if text:
            merged.append(TextRun(text=text, size=size, page=page))
    return merged


def _normalise(text: str) -> str:
    """Collapse export whitespace and turn bullet glyphs into list markers."""
    text = _BULLET_RUN.sub("\n- ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()

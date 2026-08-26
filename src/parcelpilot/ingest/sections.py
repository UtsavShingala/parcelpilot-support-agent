"""Rebuild a document outline from font sizes.

Headings are whatever is set larger than body text. The largest size seen first is
the document title; the remaining sizes become nesting levels in the order they
rank. Nothing here knows the shape of any particular document -- a corpus with
different heading sizes, different numbering, or no numbering at all parses the
same way.

Text appearing before the first heading is kept separately as the header block.
In this pack that block carries the status, effective date and account a document
applies to, which is what the authority rules read.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from parcelpilot.ingest.pdf_text import TextRun, extract_runs

# "1. Order cancellation" or "2.1) Something" -- but not "KI-208 - ..." or a bare title.
_NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+)*)[.)]\s+(.*)$")


@dataclass(frozen=True)
class Section:
    """One leaf of the document outline, with the text that sits under it."""

    number: str | None
    title: str
    text: str
    heading_path: tuple[str, ...]
    level: int
    page: int


@dataclass(frozen=True)
class ParsedDocument:
    source_file: str
    title: str
    header: str
    sections: tuple[Section, ...] = field(default_factory=tuple)


def parse_pdf(path: Path) -> ParsedDocument:
    return parse_runs(extract_runs(path), source_file=path.name)


def parse_runs(runs: list[TextRun], *, source_file: str) -> ParsedDocument:
    if not runs:
        return ParsedDocument(source_file=source_file, title="", header="")

    level_for_size = _heading_levels(runs)
    title_run = _title_run(runs, level_for_size)
    depth_for_level = _section_depths(runs, level_for_size, title_run)

    header: list[str] = []
    sections: list[Section] = []
    path: list[str] = []
    open_heading: _OpenSection | None = None

    for run in runs:
        if run is title_run:
            continue

        level = level_for_size.get(run.size)
        if level is None:  # body text belongs to whatever heading is open
            if open_heading is None:
                header.append(run.text)
            else:
                open_heading.body.append(run.text)
            continue

        if open_heading is not None:
            sections.extend(open_heading.close())
        depth = depth_for_level[level]
        path[:] = path[:depth] + [run.text]
        open_heading = _OpenSection(heading=run.text, level=depth, path=tuple(path), page=run.page)

    if open_heading is not None:
        sections.extend(open_heading.close())

    return ParsedDocument(
        source_file=source_file,
        title=title_run.text if title_run else "",
        header="\n".join(header).strip(),
        sections=tuple(sections),
    )


def _title_run(runs: list[TextRun], level_for_size: dict[float, int]) -> TextRun | None:
    """The first heading set in the largest size names the document."""
    for run in runs:
        if level_for_size.get(run.size) == 0:
            return run
    return None


def _section_depths(
    runs: list[TextRun], level_for_size: dict[float, int], title_run: TextRun | None
) -> dict[int, int]:
    """Map the heading levels that actually occur onto consecutive nesting depths.

    Levels are ranked across every above-body size in the document, including the
    title's. Sections must not inherit that offset, or the top-level headings would
    all nest under whichever one came first instead of being siblings.
    """
    levels = set()
    for run in runs:
        if run is title_run:
            continue
        level = level_for_size.get(run.size)
        if level is not None:
            levels.add(level)
    return {level: depth for depth, level in enumerate(sorted(levels))}


def _heading_levels(runs: list[TextRun]) -> dict[float, int]:
    """Map each above-body font size to a nesting level, largest first.

    Body size is the size most of the *text* is set in, weighted by character count
    rather than by run count -- a document with many short headings and few long
    paragraphs would otherwise mistake its headings for body text.
    """
    weight: Counter[float] = Counter()
    for run in runs:
        weight[run.size] += len(run.text)
    body_size = weight.most_common(1)[0][0]
    heading_sizes = sorted({run.size for run in runs if run.size > body_size}, reverse=True)
    return {size: level for level, size in enumerate(heading_sizes)}


@dataclass
class _OpenSection:
    heading: str
    level: int
    path: tuple[str, ...]
    page: int
    body: list[str] = field(default_factory=list)

    def close(self) -> list[Section]:
        """Emit this section, unless it only exists to hold sub-headings."""
        text = "\n".join(self.body).strip()
        if not text:
            return []
        match = _NUMBERED_HEADING.match(self.heading)
        number, title = (match.group(1), match.group(2)) if match else (None, self.heading)
        return [
            Section(
                number=number,
                title=title,
                text=text,
                heading_path=self.path,
                level=self.level,
                page=self.page,
            )
        ]

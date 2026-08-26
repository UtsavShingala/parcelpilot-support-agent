"""Build the retrieval artifacts from a corpus.

Run as ``python -m parcelpilot.ingest.build_index``. Everything it writes lives
under the index directory and can be deleted and rebuilt at any time.

The summary it prints is deliberately detailed: chunk counts per authority tier
are how you notice that a document was misclassified -- a deprecated policy that
silently lands in CURRENT_POLICY would otherwise only surface as a wrong answer
much later.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from parcelpilot.config import Settings, get_settings
from parcelpilot.ingest.authority import AuthorityTier
from parcelpilot.ingest.documents import Chunk, load_corpus
from parcelpilot.ingest.workbook import Workbook, build_database


@dataclass(frozen=True)
class BuildResult:
    chunks: list[Chunk]
    workbook: Workbook | None
    chunks_path: Path
    database_path: Path | None

    @property
    def tier_counts(self) -> dict[str, int]:
        counts = Counter(chunk.tier.name for chunk in self.chunks)
        return {tier.name: counts.get(tier.name, 0) for tier in AuthorityTier}

    @property
    def document_count(self) -> int:
        return len({chunk.source_file for chunk in self.chunks})


def build(settings: Settings | None = None) -> BuildResult:
    """Parse the corpus and write the chunk store and database."""
    settings = settings or get_settings()
    corpus_dir = settings.corpus_dir
    if not corpus_dir.exists():
        raise FileNotFoundError(f"corpus directory {corpus_dir} does not exist; see data/README.md")

    settings.index_path.mkdir(parents=True, exist_ok=True)

    chunks = load_corpus(corpus_dir)
    settings.chunks_path.write_text(
        json.dumps([chunk.to_dict() for chunk in chunks], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    workbook: Workbook | None = None
    database_path: Path | None = None
    sources = sorted(corpus_dir.glob("*.xlsx"))
    if sources:
        workbook = build_database(sources[0], settings.database_path)
        database_path = settings.database_path

    return BuildResult(
        chunks=chunks,
        workbook=workbook,
        chunks_path=settings.chunks_path,
        database_path=database_path,
    )


def load_chunks(settings: Settings | None = None) -> list[Chunk]:
    """Read the chunk store written by :func:`build`, building it if it is missing."""
    settings = settings or get_settings()
    if not settings.chunks_path.exists():
        return build(settings).chunks
    payload = json.loads(settings.chunks_path.read_text(encoding="utf-8"))
    return [Chunk.from_dict(item) for item in payload]


def _report(result: BuildResult) -> str:
    lines = [
        f"documents  {result.document_count}",
        f"chunks     {len(result.chunks)} -> {result.chunks_path}",
    ]
    lines += [
        f"  {tier:<15} {count}" for tier, count in result.tier_counts.items() if count
    ]
    if result.workbook and result.database_path:
        lines.append(f"database   {result.database_path}")
        lines.append(f"  snapshot        {result.workbook.snapshot_at.isoformat()}")
        for table, rows in result.workbook.sheets.items():
            lines.append(f"  {table:<15} {len(rows)} rows")
    else:
        lines.append("database   (no workbook found in the corpus)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", help="corpus name to build (default: the CORPUS setting)")
    arguments = parser.parse_args(argv)

    settings = get_settings()
    if arguments.corpus:
        settings = settings.model_copy(update={"corpus": arguments.corpus})

    print(_report(build(settings)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

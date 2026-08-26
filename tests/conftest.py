"""Shared fixtures.

The source document pack is not committed (see data/README.md), so tests that
need it skip rather than fail on a fresh clone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from parcelpilot.config import get_settings


@pytest.fixture(scope="session")
def corpus_dir() -> Path:
    settings = get_settings()
    directory = settings.corpus_dir
    if not directory.exists() or not any(directory.glob("*.pdf")):
        pytest.skip(f"no source documents in {directory}; see data/README.md")
    return directory


@pytest.fixture(scope="session")
def pdf_paths(corpus_dir: Path) -> list[Path]:
    return sorted(corpus_dir.glob("*.pdf"))

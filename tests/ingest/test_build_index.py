"""The build must produce artifacts that reload into exactly what was written."""

from __future__ import annotations

from pathlib import Path

import pytest

from parcelpilot.config import Settings, get_settings
from parcelpilot.ingest.authority import AuthorityTier
from parcelpilot.ingest.build_index import build, load_chunks, main


@pytest.fixture
def settings(tmp_path: Path, corpus_dir: Path) -> Settings:
    return Settings(index_dir=tmp_path / "index")


def test_build_writes_both_artifacts(settings: Settings) -> None:
    result = build(settings)

    assert result.chunks
    assert settings.chunks_path.exists()
    assert settings.database_path.exists()
    assert result.document_count > 1


def test_chunks_reload_identically(settings: Settings) -> None:
    written = build(settings).chunks
    assert load_chunks(settings) == written


def test_loading_without_a_prior_build_builds_first(settings: Settings) -> None:
    assert not settings.chunks_path.exists()
    assert load_chunks(settings)
    assert settings.chunks_path.exists()


def test_every_tier_present_in_the_corpus_is_counted(settings: Settings) -> None:
    """Tier counts are the check that a document was not silently misclassified."""
    counts = build(settings).tier_counts

    assert counts[AuthorityTier.CURRENT_POLICY.name] > 0
    assert counts[AuthorityTier.AGREEMENT.name] > 0
    assert counts[AuthorityTier.DEPRECATED.name] > 0
    assert sum(counts.values()) == len(build(settings).chunks)


def test_rebuilding_is_idempotent(settings: Settings) -> None:
    first = build(settings).chunks
    assert build(settings).chunks == first


def test_a_missing_corpus_fails_with_a_pointer_to_the_setup_notes(tmp_path: Path) -> None:
    absent = Settings(data_dir=tmp_path / "nothing", index_dir=tmp_path / "index")
    with pytest.raises(FileNotFoundError, match="data/README.md"):
        build(absent)


def test_the_command_line_entry_point_reports_what_it_built(
    capsys: pytest.CaptureFixture[str],
    corpus_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Built into a temporary index, not the real one.

    The entry point reads settings from the environment, so without this it would
    rewrite the working index -- which fails outright on Windows if a dev server
    happens to have the database open, and is rude to whoever is using it either way.
    """
    monkeypatch.setenv("INDEX_DIR", str(tmp_path / "index"))
    get_settings.cache_clear()
    monkeypatch.setattr(
        "parcelpilot.ingest.build_index.get_settings",
        lambda: Settings(index_dir=tmp_path / "index"),
    )

    assert main([]) == 0
    report = capsys.readouterr().out
    assert "documents" in report
    assert "chunks" in report
    assert "snapshot" in report

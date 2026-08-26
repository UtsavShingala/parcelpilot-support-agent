"""The workbook decides what "now" means, so its snapshot must survive ingest intact."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook as OpenpyxlWorkbook

from parcelpilot.ingest.workbook import (
    METADATA_TABLE,
    SNAPSHOT_KEY,
    build_database,
    read_workbook,
)


@pytest.fixture(scope="session")
def workbook_path(corpus_dir: Path) -> Path:
    sources = sorted(corpus_dir.glob("*.xlsx"))
    if not sources:
        pytest.skip(f"no workbook in {corpus_dir}")
    return sources[0]


def test_the_snapshot_time_is_read_with_its_timezone(workbook_path: Path) -> None:
    snapshot = read_workbook(workbook_path).snapshot_at
    assert snapshot.tzinfo is not None, "the snapshot must be an unambiguous instant"
    assert snapshot.utcoffset() is not None


def test_naive_timestamps_gain_the_snapshot_offset(workbook_path: Path) -> None:
    """Sheets hold local times; downstream SLA maths must not have to guess a zone."""
    workbook = read_workbook(workbook_path)
    offset = workbook.snapshot_at.strftime("%z")
    formatted = f"{offset[:3]}:{offset[3:]}"

    stamps = [
        value
        for rows in workbook.sheets.values()
        for row in rows
        for value in row.values()
        if isinstance(value, str) and value[:2].isdigit() and "T" in value
    ]
    assert stamps, "no timestamps were found to check"
    assert all(stamp.endswith(formatted) for stamp in stamps)


def _build(tmp_path: Path, sheets: dict[str, list[list[Any]]]) -> Path:
    book = OpenpyxlWorkbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    path = tmp_path / "book.xlsx"
    book.save(path)
    return path


def test_an_unfamiliar_workbook_loads_without_code_changes(tmp_path: Path) -> None:
    """Sheet and column names come from the data, not from a hard-coded schema."""
    source = _build(
        tmp_path,
        {
            "README": [["Dataset snapshot", "2030-01-02 08:30 Europe/Berlin"]],
            "Depot Runs": [
                ["Run ID", "Late?", "Cost", "Left At"],
                ["RUN-1", True, 12.5, "2030-01-02 07:00"],
                ["RUN-2", False, 9.0, None],
            ],
        },
    )
    workbook = build_database(source, tmp_path / "out.db")

    assert set(workbook.sheets) == {"depot_runs"}
    first = workbook.sheets["depot_runs"][0]
    assert first == {
        "run_id": "RUN-1",
        "late": 1,
        "cost": 12.5,
        "left_at": "2030-01-02T07:00:00+01:00",
    }

    connection = sqlite3.connect(tmp_path / "out.db")
    try:
        types = {row[1]: row[2] for row in connection.execute('PRAGMA table_info("depot_runs")')}
        assert types == {
            "run_id": "TEXT",
            "late": "INTEGER",
            "cost": "REAL",
            "left_at": "TEXT",
        }
        stored = dict(connection.execute("SELECT key, value FROM corpus_meta"))
        assert stored[SNAPSHOT_KEY] == "2030-01-02T08:30:00+01:00"
    finally:
        connection.close()


def test_a_workbook_without_a_snapshot_is_rejected(tmp_path: Path) -> None:
    """Guessing the reference time would silently corrupt every SLA answer."""
    source = _build(
        tmp_path,
        {
            "README": [["Currency", "INR"]],
            "orders": [["order_id"], ["ORD-1"]],
        },
    )
    with pytest.raises(ValueError, match="snapshot"):
        read_workbook(source)


def test_a_snapshot_without_a_timezone_falls_back_to_utc(tmp_path: Path) -> None:
    source = _build(
        tmp_path,
        {
            "README": [["Dataset snapshot", "2030-01-02 08:30"]],
            "orders": [["order_id"], ["ORD-1"]],
        },
    )
    assert read_workbook(source).snapshot_at.isoformat() == "2030-01-02T08:30:00+00:00"


def test_the_readme_sheet_is_metadata_rather_than_a_table(workbook_path: Path) -> None:
    workbook = read_workbook(workbook_path)
    assert "readme" not in workbook.sheets
    assert workbook.metadata, "README rows were dropped instead of being kept as metadata"


def test_rebuilding_replaces_the_previous_database(tmp_path: Path, workbook_path: Path) -> None:
    target = tmp_path / "out.db"
    build_database(workbook_path, target)
    first = _row_counts(target)
    build_database(workbook_path, target)
    assert _row_counts(target) == first, "rebuilding duplicated rows instead of replacing them"


def _row_counts(db_path: Path) -> dict[str, int]:
    connection = sqlite3.connect(db_path)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name != ?",
                (METADATA_TABLE,),
            )
        ]
        return {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }
    finally:
        connection.close()

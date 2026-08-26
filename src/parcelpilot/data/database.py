"""Connection handling for the database ingest builds.

The snapshot time lives here rather than being passed around, because it is the
only clock this system has. Every elapsed-time answer -- SLA breach, ticket age,
how long ago an order was booked -- is measured from it, and reading the machine
clock instead would make answers drift as the pack ages while still sounding
confident.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from parcelpilot.config import Settings, get_settings
from parcelpilot.ingest.workbook import METADATA_TABLE, SNAPSHOT_KEY


def connect(settings: Settings | None = None) -> sqlite3.Connection:
    """Open the built database read-only-ish, with rows that behave like mappings."""
    settings = settings or get_settings()
    path = settings.database_path
    if not path.exists():
        raise FileNotFoundError(
            f"{path} has not been built; run python -m parcelpilot.ingest.build_index"
        )
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def snapshot_time(connection: sqlite3.Connection) -> datetime:
    """The instant the dataset was captured. The only "now" this system recognises."""
    row = connection.execute(
        f'SELECT value FROM "{METADATA_TABLE}" WHERE key = ?', (SNAPSHOT_KEY,)
    ).fetchone()
    if row is None:
        raise ValueError(
            "the database records no snapshot time; every elapsed-time answer depends "
            "on it, so it cannot be defaulted"
        )
    return datetime.fromisoformat(row["value"])


def table_names(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    )
    return [row["name"] for row in rows]


def database_path(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).database_path

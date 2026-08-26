"""The sign-in roster must follow the data, since evaluators may swap the records."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from parcelpilot.auth.context import Role
from parcelpilot.auth.personas import find_persona, load_personas, open_personas
from parcelpilot.data.database import connect


@pytest.fixture(scope="module")
def connection(corpus_dir: Path) -> Iterator[sqlite3.Connection]:
    handle = connect()
    yield handle
    handle.close()


def test_every_account_gets_a_customer_persona(connection: sqlite3.Connection) -> None:
    accounts = {
        row["account_id"] for row in connection.execute("SELECT account_id FROM accounts")
    }
    customers = {
        persona.context.account_id
        for persona in load_personas(connection)
        if persona.role is Role.CUSTOMER
    }
    assert customers == accounts


def test_support_personas_come_from_who_handles_tickets(
    connection: sqlite3.Connection,
) -> None:
    assignees = {
        str(row["assigned_to"]).strip()
        for row in connection.execute(
            "SELECT DISTINCT assigned_to FROM tickets WHERE assigned_to IS NOT NULL"
        )
    }
    agents = {
        persona.label
        for persona in load_personas(connection)
        if persona.role is Role.SUPPORT_AGENT
    }
    assert agents == assignees
    assert agents, "the corpus should assign tickets to someone"


def test_exactly_one_operations_manager_is_offered(connection: sqlite3.Connection) -> None:
    managers = [
        persona for persona in load_personas(connection) if persona.role is Role.OPS_MANAGER
    ]
    assert len(managers) == 1
    assert managers[0].context.account_id is None


def test_persona_ids_are_unique(connection: sqlite3.Connection) -> None:
    ids = [persona.persona_id for persona in load_personas(connection)]
    assert len(ids) == len(set(ids))


def test_customer_personas_carry_a_scoped_context(connection: sqlite3.Connection) -> None:
    for persona in load_personas(connection):
        if persona.role is not Role.CUSTOMER:
            continue
        scope = persona.context.account_scope()
        assert scope.permits(persona.context.account_id or "")
        assert not scope.unrestricted


def test_internal_personas_carry_an_unrestricted_context(
    connection: sqlite3.Connection,
) -> None:
    for persona in load_personas(connection):
        if persona.role is Role.CUSTOMER:
            continue
        assert persona.context.account_scope().unrestricted


def test_a_persona_can_be_looked_up_by_id(connection: sqlite3.Connection) -> None:
    personas = load_personas(connection)
    first = personas[0]
    assert find_persona(personas, first.persona_id) == first
    assert find_persona(personas, "nobody") is None


def test_the_roster_follows_an_edited_roster(tmp_path: Path) -> None:
    """Rename an account and the sign-in list must change with it."""
    path = tmp_path / "edited.db"
    handle = sqlite3.connect(path)
    handle.execute("CREATE TABLE accounts (account_id TEXT, account_name TEXT, plan TEXT)")
    handle.execute("INSERT INTO accounts VALUES ('ACCT-900', 'Meridian Freight', 'Growth')")
    handle.execute("CREATE TABLE tickets (assigned_to TEXT)")
    handle.execute("INSERT INTO tickets VALUES ('Devi')")
    handle.commit()
    handle.row_factory = sqlite3.Row

    personas = load_personas(handle)
    handle.close()

    labels = {persona.label for persona in personas}
    assert "Meridian Freight" in labels
    assert "Devi" in labels
    assert "Northstar Logistics" not in labels


def test_the_roster_serialises_for_a_login_screen(corpus_dir: Path) -> None:
    """Only what a picker needs: no contract terms, no contact names, no notes."""
    for persona in open_personas():
        payload = persona.to_dict()
        assert set(payload) == {
            "persona_id",
            "label",
            "description",
            "role",
            "account_id",
        }
        assert all(isinstance(value, str) for value in payload.values())

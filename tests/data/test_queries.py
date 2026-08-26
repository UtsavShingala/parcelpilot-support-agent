"""Scoping must live in the SQL, so unauthorised rows are never fetched at all."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from parcelpilot.auth.context import CallerContext, Role
from parcelpilot.data.queries import OperationalData, ScopePredicate

CUSTOMER_ONE = CallerContext(role=Role.CUSTOMER, account_id="ACCT-001")
CUSTOMER_TWO = CallerContext(role=Role.CUSTOMER, account_id="ACCT-002")
SUPPORT = CallerContext(role=Role.SUPPORT_AGENT)


@pytest.fixture(scope="module")
def data(corpus_dir: Path) -> Iterator[OperationalData]:
    operational = OperationalData.open()
    yield operational
    operational.close()


def test_the_predicate_restricts_a_customer_to_their_account() -> None:
    predicate = ScopePredicate.for_caller(CUSTOMER_ONE)
    assert predicate.sql == "account_id = ?"
    assert predicate.parameters == ("ACCT-001",)


def test_the_predicate_is_open_for_internal_staff() -> None:
    assert ScopePredicate.for_caller(SUPPORT).sql == "1 = 1"


def test_a_caller_with_no_account_matches_nothing() -> None:
    """Fail closed: a broken upstream must yield no rows, not all of them."""
    unscoped = CallerContext.__new__(CallerContext)
    object.__setattr__(unscoped, "role", Role.CUSTOMER)
    object.__setattr__(unscoped, "account_id", None)
    object.__setattr__(unscoped, "display_name", "")

    assert ScopePredicate.for_caller(unscoped).sql == "1 = 0"


def test_a_customer_sees_only_their_own_orders(data: OperationalData) -> None:
    for caller in (CUSTOMER_ONE, CUSTOMER_TWO):
        rows = data.orders(caller, limit=100)
        assert rows, f"{caller.account_id} has no orders to check"
        assert {row["account_id"] for row in rows} == {caller.account_id}


def test_a_customer_sees_only_their_own_tickets(data: OperationalData) -> None:
    for caller in (CUSTOMER_ONE, CUSTOMER_TWO):
        rows = data.tickets(caller, limit=100)
        assert rows
        assert {row["account_id"] for row in rows} == {caller.account_id}


def test_internal_staff_see_every_account(data: OperationalData) -> None:
    everyone = {row["account_id"] for row in data.orders(SUPPORT, limit=100)}
    assert len(everyone) > 1


def test_naming_another_account_does_not_widen_a_customer(data: OperationalData) -> None:
    """The scope predicate is ANDed, so an explicit filter can only narrow."""
    assert data.orders(CUSTOMER_ONE, account_id="ACCT-002", limit=100) == []


def test_fetching_another_account_order_by_id_returns_nothing(data: OperationalData) -> None:
    stolen = data.orders(SUPPORT, account_id="ACCT-002", limit=1)
    assert stolen, "expected at least one order on the other account"
    order_id = stolen[0]["order_id"]

    assert data.order(CUSTOMER_ONE, order_id) is None
    assert data.order(CUSTOMER_TWO, order_id) is not None


def test_fetching_another_account_ticket_by_id_returns_nothing(data: OperationalData) -> None:
    stolen = data.tickets(SUPPORT, account_id="ACCT-002", limit=1)
    assert stolen
    ticket_id = stolen[0]["ticket_id"]

    assert data.ticket(CUSTOMER_ONE, ticket_id) is None
    assert not data.ticket_exists(CUSTOMER_ONE, ticket_id)
    assert data.ticket_exists(CUSTOMER_TWO, ticket_id)


def test_a_limit_counts_the_callers_own_rows(data: OperationalData) -> None:
    """A post-filter would spend the limit on other accounts' rows first."""
    rows = data.orders(CUSTOMER_ONE, limit=2)
    assert len(rows) == 2
    assert {row["account_id"] for row in rows} == {"ACCT-001"}


def test_accounts_are_scoped_too(data: OperationalData) -> None:
    assert [row["account_id"] for row in data.accounts(CUSTOMER_ONE)] == ["ACCT-001"]
    assert len(data.accounts(SUPPORT)) > 1
    assert data.account(CUSTOMER_ONE, "ACCT-002") is None


def test_status_filters_are_case_insensitive(data: OperationalData) -> None:
    lower = data.orders(SUPPORT, status="booked", limit=100)
    upper = data.orders(SUPPORT, status="BOOKED", limit=100)
    assert lower == upper
    assert lower, "the corpus should contain booked orders"


def test_historical_resolutions_travel_with_a_warning(data: OperationalData) -> None:
    """The workbook says some are wrong, so they must never arrive looking like policy."""
    with_history = [
        row for row in data.tickets(SUPPORT, limit=100) if row.get("historical_resolution")
    ]
    assert with_history, "the corpus should contain resolved tickets"
    assert all("incorrect" in row["historical_resolution_warning"] for row in with_history)


def test_tickets_without_a_resolution_carry_no_warning(data: OperationalData) -> None:
    plain = [
        row for row in data.tickets(SUPPORT, limit=100) if not row.get("historical_resolution")
    ]
    assert plain
    assert all("historical_resolution_warning" not in row for row in plain)


def test_the_snapshot_time_is_the_reference_clock(data: OperationalData) -> None:
    assert data.snapshot_at.tzinfo is not None
    assert data.snapshot_at.year >= 2020

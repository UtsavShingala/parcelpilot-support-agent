"""The caller context is the only thing standing between two customers' data."""

from __future__ import annotations

import dataclasses

import pytest

from parcelpilot.auth.context import CallerContext, Role
from parcelpilot.retrieval.scope import AccountScope


def test_a_customer_is_scoped_to_their_own_account() -> None:
    caller = CallerContext(role=Role.CUSTOMER, account_id="ACCT-001")
    scope = caller.account_scope()

    assert scope.permits("ACCT-001")
    assert not scope.permits("ACCT-002")


def test_internal_staff_reach_every_account() -> None:
    for role in (Role.SUPPORT_AGENT, Role.OPS_MANAGER):
        scope = CallerContext(role=role).account_scope()
        assert scope.permits("ACCT-001")
        assert scope.permits("ACCT-002")
        assert scope.unrestricted


def test_a_customer_context_requires_an_account() -> None:
    """A customer with no account would otherwise silently become a no-scope caller."""
    with pytest.raises(ValueError, match="account"):
        CallerContext(role=Role.CUSTOMER)


def test_an_internal_context_may_not_be_pinned_to_an_account() -> None:
    """It would read as a restriction, and nothing downstream enforces it."""
    with pytest.raises(ValueError, match="internal"):
        CallerContext(role=Role.SUPPORT_AGENT, account_id="ACCT-001")


def test_the_context_is_frozen() -> None:
    caller = CallerContext(role=Role.CUSTOMER, account_id="ACCT-001")
    with pytest.raises(dataclasses.FrozenInstanceError):
        caller.account_id = "ACCT-002"  # type: ignore[misc]


def test_scope_is_derived_rather_than_reimplemented() -> None:
    """One rule, one home: the context must not grow its own copy of the logic."""
    caller = CallerContext(role=Role.CUSTOMER, account_id="ACCT-007")
    assert caller.account_scope() == AccountScope.for_accounts("ACCT-007")
    assert CallerContext(role=Role.OPS_MANAGER).account_scope() == (
        AccountScope.unrestricted_access()
    )


def test_roles_split_into_customer_and_internal() -> None:
    assert CallerContext(role=Role.CUSTOMER, account_id="ACCT-001").is_customer
    assert not CallerContext(role=Role.CUSTOMER, account_id="ACCT-001").is_internal
    assert CallerContext(role=Role.SUPPORT_AGENT).is_internal
    assert CallerContext(role=Role.OPS_MANAGER).is_internal


def test_a_caller_describes_itself_for_logging() -> None:
    caller = CallerContext(
        role=Role.CUSTOMER, account_id="ACCT-001", display_name="Northstar Logistics"
    )
    assert caller.describe() == "Northstar Logistics (customer, ACCT-001)"
    assert CallerContext(role=Role.OPS_MANAGER).describe() == (
        "ops_manager (ops_manager, all accounts)"
    )

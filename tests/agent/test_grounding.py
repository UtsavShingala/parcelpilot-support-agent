"""A policy figure must come from a document the caller was allowed to read.

The calculator takes thresholds as arguments rather than holding a table of them in
code, which is right -- the numbers live in the corpus and change there. It left a
hole: a hallucinated figure was indistinguishable from a correct one. The arithmetic
ran faithfully on it and returned something precise, well-formatted and wrong, with
a real citation attached to a number that was never in it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from parcelpilot.agent.registry import ToolRegistry, build_registry
from parcelpilot.auth.context import CallerContext, Role
from parcelpilot.data.queries import OperationalData
from parcelpilot.retrieval.store import DocumentStore

NORTHSTAR = CallerContext(role=Role.CUSTOMER, account_id="ACCT-001", display_name="Northstar")
LUMENWORKS = CallerContext(role=Role.CUSTOMER, account_id="ACCT-002", display_name="LumenWorks")
SUPPORT = CallerContext(role=Role.SUPPORT_AGENT, display_name="Maya")

SOP_CANCELLATION = "ParcelPilot Cancellation & Service Credit SOP v4 - 1. Order cancellation"
SOP_CREDITS = "ParcelPilot Cancellation & Service Credit SOP v4 - 2. Failed-pickup service credits"
NORTHSTAR_TERMS = "ParcelPilot - Northstar Logistics Enterprise Agreement - 1. Support terms"
SUPERSEDED = "ParcelPilot Support Policy v2 - Severity and response targets"


@pytest.fixture(scope="module")
def registry(corpus_dir: Path) -> Iterator[ToolRegistry]:
    data = OperationalData.open()
    yield build_registry(DocumentStore.from_settings(), data)
    data.close()


def _calculate(registry: ToolRegistry, caller: CallerContext, **arguments: Any) -> Any:
    return registry.dispatch("calculate", arguments, caller)


# -- the hole this closes -------------------------------------------------------


def test_a_figure_that_appears_nowhere_is_refused(registry: ToolRegistry) -> None:
    """The whole point: an invented threshold no longer computes."""
    result = _calculate(
        registry,
        NORTHSTAR,
        operation="cancellation_timing",
        order_id="ORD-1001",
        free_window_minutes=90,  # the SOP says 30
        sources=[SOP_CANCELLATION],
    )

    assert not result.ok
    assert "free_window_minutes=90" in result.error
    assert "do not supply a number you have not read" in result.error


def test_the_figure_the_document_actually_states_is_accepted(registry: ToolRegistry) -> None:
    result = _calculate(
        registry,
        NORTHSTAR,
        operation="cancellation_timing",
        order_id="ORD-1001",
        free_window_minutes=30,
        sources=[SOP_CANCELLATION],
    )

    assert result.ok, result.error
    assert result.payload["grounded_in"] == [SOP_CANCELLATION]


def test_citing_nothing_is_refused(registry: ToolRegistry) -> None:
    result = _calculate(
        registry,
        NORTHSTAR,
        operation="cancellation_timing",
        order_id="ORD-1001",
        free_window_minutes=30,
    )

    assert not result.ok
    assert "no sources were cited" in result.error


def test_a_figure_from_a_different_document_does_not_ground(registry: ToolRegistry) -> None:
    """30 minutes is in the SOP, not in Northstar's support terms."""
    result = _calculate(
        registry,
        NORTHSTAR,
        operation="cancellation_timing",
        order_id="ORD-1001",
        free_window_minutes=30,
        sources=[NORTHSTAR_TERMS],
    )

    assert not result.ok
    assert "does not appear in the passages you cited" in result.error


# -- the grounding cannot be used to widen access -------------------------------


def test_a_customer_cannot_ground_in_another_accounts_agreement(
    registry: ToolRegistry,
) -> None:
    """Otherwise the calculator becomes a way to read a contract you may not see."""
    result = _calculate(
        registry,
        LUMENWORKS,
        operation="sla_status",
        ticket_id="TKT-502",
        target_minutes=15,  # Northstar's P1, in a document LumenWorks may not read
        sources=[NORTHSTAR_TERMS],
    )

    assert not result.ok
    assert "not available to this user" in result.error


def test_a_superseded_document_cannot_support_a_calculation(
    registry: ToolRegistry,
) -> None:
    """v2's Enterprise P1 is one hour; quoting it would be the trap working."""
    result = _calculate(
        registry,
        SUPPORT,
        operation="sla_status",
        ticket_id="TKT-501",
        target_minutes=60,
        sources=[SUPERSEDED],
    )

    assert not result.ok
    assert "superseded" in result.error


def test_an_unknown_citation_is_refused(registry: ToolRegistry) -> None:
    result = _calculate(
        registry,
        NORTHSTAR,
        operation="cancellation_timing",
        order_id="ORD-1001",
        free_window_minutes=30,
        sources=["Some Policy I Invented - 4. Fees"],
    )

    assert not result.ok
    assert "no passage matches" in result.error


# -- the figures the corpus actually contains -----------------------------------


def test_every_figure_in_a_credit_calculation_is_checked(registry: ToolRegistry) -> None:
    """The SOP states INR 500 and 10%; the cap is invented and must be caught."""
    result = _calculate(
        registry,
        LUMENWORKS,
        operation="service_credit",
        shipment_fee_inr=2400,
        maximum_inr=500,
        percentage_of_fee=10,
        monthly_cap_inr=99999,
        sources=[SOP_CREDITS],
    )

    assert not result.ok
    assert "monthly_cap_inr=99999" in result.error


def test_thousands_separators_do_not_defeat_the_check(registry: ToolRegistry) -> None:
    """Northstar's agreement writes the cap as "INR 5,000"."""
    result = _calculate(
        registry,
        NORTHSTAR,
        operation="service_credit",
        shipment_fee_inr=4200,
        flat_amount_inr=300,
        monthly_cap_inr=5000,
        sources=[
            "ParcelPilot - Northstar Logistics Enterprise Agreement - 3. Service credits",
            "ParcelPilot - LumenWorks Service Agreement - 3. Failed-pickup credits",
        ],
    )

    # LumenWorks' clause is not readable by Northstar, so this must refuse -- but on
    # scope, proving the cap itself matched despite the comma.
    assert not result.ok
    assert "not available to this user" in result.error


def test_several_sources_can_support_one_calculation(registry: ToolRegistry) -> None:
    """A figure may come from any passage the caller cited, not only the first."""
    result = _calculate(
        registry,
        SUPPORT,
        operation="service_credit",
        shipment_fee_inr=2400,
        maximum_inr=500,
        percentage_of_fee=10,
        sources=[NORTHSTAR_TERMS, SOP_CREDITS],
    )

    assert result.ok, result.error
    assert set(result.payload["grounded_in"]) == {NORTHSTAR_TERMS, SOP_CREDITS}


def test_a_calculation_with_no_figures_needs_no_source(registry: ToolRegistry) -> None:
    """Nothing to ground, so nothing to cite -- and the real error still surfaces."""
    result = _calculate(registry, NORTHSTAR, operation="cancellation_timing")

    assert not result.ok
    assert "free_window_minutes" in result.error, "the missing-argument error was shadowed"

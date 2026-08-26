"""Arithmetic must be deterministic, scoped, and anchored to the snapshot."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from parcelpilot.agent.calculate import (
    CalculationError,
    cancellation_timing,
    pickup_delay,
    service_credit_amount,
    sla_status,
)
from parcelpilot.auth.context import CallerContext, Role
from parcelpilot.data.queries import OperationalData

SUPPORT = CallerContext(role=Role.SUPPORT_AGENT)


@pytest.fixture(scope="module")
def data(corpus_dir: Path) -> Iterator[OperationalData]:
    operational = OperationalData.open()
    yield operational
    operational.close()


def _an_order(data: OperationalData, **filters: str) -> dict:
    rows = data.orders(SUPPORT, limit=100, **filters)
    if not rows:
        pytest.skip(f"no order matching {filters}")
    return rows[0]


# -- time basis -----------------------------------------------------------------


def test_every_result_reports_the_snapshot_as_its_reference(data: OperationalData) -> None:
    """Nothing here may consult the wall clock."""
    order = _an_order(data)
    result = cancellation_timing(
        data, SUPPORT, order_id=order["order_id"], free_window_minutes=30
    )
    assert result["reference_time"] == data.snapshot_at.isoformat()


def test_cancellation_is_measured_to_the_request_not_the_snapshot(
    data: OperationalData,
) -> None:
    """A customer who asked in time must not lose the waiver to a slow queue."""
    requested = next(
        (
            order
            for order in data.orders(SUPPORT, limit=100)
            if order.get("cancellation_requested_at")
        ),
        None,
    )
    assert requested is not None

    result = cancellation_timing(
        data, SUPPORT, order_id=requested["order_id"], free_window_minutes=30
    )
    assert result["measured_to_basis"] == "cancellation request"
    assert result["measured_to"] == requested["cancellation_requested_at"]
    assert result["measured_to"] != result["reference_time"]


def test_cancellation_falls_back_to_the_snapshot_when_nothing_was_requested(
    data: OperationalData,
) -> None:
    pending = next(
        (
            order
            for order in data.orders(SUPPORT, limit=100)
            if not order.get("cancellation_requested_at")
        ),
        None,
    )
    assert pending is not None

    result = cancellation_timing(
        data, SUPPORT, order_id=pending["order_id"], free_window_minutes=30
    )
    assert result["measured_to_basis"] == "dataset snapshot"
    assert result["measured_to"] == result["reference_time"]


def test_the_free_window_boundary_is_inclusive(data: OperationalData) -> None:
    order = _an_order(data)
    elapsed = cancellation_timing(
        data, SUPPORT, order_id=order["order_id"], free_window_minutes=0
    )["minutes_since_booking"]

    exactly = cancellation_timing(
        data, SUPPORT, order_id=order["order_id"], free_window_minutes=elapsed
    )
    just_under = cancellation_timing(
        data, SUPPORT, order_id=order["order_id"], free_window_minutes=elapsed - 1
    )
    assert exactly["within_free_window"]
    assert not just_under["within_free_window"]


# -- pickup delay ---------------------------------------------------------------


def test_a_pickup_still_awaited_is_measured_to_the_snapshot(data: OperationalData) -> None:
    awaiting = next(
        (
            order
            for order in data.orders(SUPPORT, limit=100)
            if not order.get("pickup_actual_at")
        ),
        None,
    )
    assert awaiting is not None

    result = pickup_delay(data, SUPPORT, order_id=awaiting["order_id"], threshold_hours=2)
    assert result["awaiting_pickup"]
    assert result["measured_to_basis"] == "dataset snapshot"


def test_a_completed_pickup_is_measured_to_when_it_happened(data: OperationalData) -> None:
    collected = next(
        (order for order in data.orders(SUPPORT, limit=100) if order.get("pickup_actual_at")),
        None,
    )
    assert collected is not None

    result = pickup_delay(data, SUPPORT, order_id=collected["order_id"], threshold_hours=2)
    assert not result["awaiting_pickup"]
    assert result["measured_to"] == collected["pickup_actual_at"]


def test_an_early_pickup_is_not_reported_as_negative_lateness(
    data: OperationalData,
) -> None:
    for order in data.orders(SUPPORT, limit=100):
        result = pickup_delay(data, SUPPORT, order_id=order["order_id"], threshold_hours=2)
        assert result["hours_late"] >= 0


def test_the_threshold_decides_nothing_but_the_flag(data: OperationalData) -> None:
    """Changing the threshold must not change the measured delay."""
    order = _an_order(data)
    lenient = pickup_delay(data, SUPPORT, order_id=order["order_id"], threshold_hours=100)
    strict = pickup_delay(data, SUPPORT, order_id=order["order_id"], threshold_hours=0)
    assert lenient["hours_late"] == strict["hours_late"]
    assert not lenient["exceeds_threshold"]


# -- credit amounts -------------------------------------------------------------


def test_the_lower_of_a_flat_amount_and_a_percentage_wins() -> None:
    result = service_credit_amount(
        shipment_fee_inr=2400, flat_amount_inr=500, percentage_of_fee=10
    )
    assert result["credit_inr"] == 240.0
    assert result["chosen_basis"] == "percentage of fee"


def test_the_flat_amount_wins_when_it_is_lower() -> None:
    result = service_credit_amount(
        shipment_fee_inr=50_000, flat_amount_inr=500, percentage_of_fee=10
    )
    assert result["credit_inr"] == 500.0
    assert result["chosen_basis"] == "flat amount"


def test_a_contract_naming_a_fixed_credit_replaces_the_formula() -> None:
    """Supplying only a flat amount must not silently reintroduce the SOP percentage."""
    result = service_credit_amount(shipment_fee_inr=2400, flat_amount_inr=300)
    assert result["credit_inr"] == 300.0
    assert set(result["candidates_inr"]) == {"flat amount"}


def test_a_monthly_cap_limits_what_remains() -> None:
    result = service_credit_amount(
        shipment_fee_inr=50_000,
        flat_amount_inr=500,
        monthly_cap_inr=5_000,
        credits_already_issued_inr=4_800,
    )
    assert result["credit_inr"] == 200.0
    assert result["remaining_under_cap_inr"] == 200.0
    assert "monthly cap" in " ".join(result["notes"])


def test_an_exhausted_cap_yields_nothing_rather_than_a_negative_credit() -> None:
    result = service_credit_amount(
        flat_amount_inr=500, monthly_cap_inr=5_000, credits_already_issued_inr=6_000
    )
    assert result["credit_inr"] == 0.0


def test_a_credit_over_the_approval_threshold_is_flagged() -> None:
    result = service_credit_amount(flat_amount_inr=1_500, approval_threshold_inr=1_000)
    assert result["requires_manager_approval"]
    assert "approve" in " ".join(result["notes"])


def test_a_percentage_without_a_fee_is_refused() -> None:
    with pytest.raises(CalculationError, match="shipment fee"):
        service_credit_amount(percentage_of_fee=10)


def test_a_credit_with_no_formula_is_refused() -> None:
    """Inventing a default here would be inventing policy."""
    with pytest.raises(CalculationError, match="formula"):
        service_credit_amount(shipment_fee_inr=2400)


# -- SLA ------------------------------------------------------------------------


def test_a_breach_reports_how_far_over_it_ran(data: OperationalData) -> None:
    ticket = data.tickets(SUPPORT, limit=1)[0]
    result = sla_status(data, SUPPORT, ticket_id=ticket["ticket_id"], target_minutes=0)
    assert result["breached"]
    assert result["minutes_over_target"] == result["elapsed_minutes"]
    assert result["minutes_remaining"] == 0.0


def test_a_target_still_running_reports_what_is_left(data: OperationalData) -> None:
    ticket = data.tickets(SUPPORT, limit=1)[0]
    result = sla_status(data, SUPPORT, ticket_id=ticket["ticket_id"], target_minutes=100_000)
    assert not result["breached"]
    assert result["minutes_remaining"] > 0
    assert result["minutes_over_target"] == 0.0


def test_a_business_hours_target_is_flagged_for_a_human(data: OperationalData) -> None:
    """The pack records no working calendar, so wall-clock elapsed may overstate it."""
    ticket = data.tickets(SUPPORT, limit=1)[0]
    result = sla_status(
        data,
        SUPPORT,
        ticket_id=ticket["ticket_id"],
        target_minutes=240,
        target_is_business_hours=True,
    )
    assert result["needs_human_confirmation"]
    assert "business hours" in (result["caveat"] or "")


def test_a_wall_clock_target_carries_no_caveat(data: OperationalData) -> None:
    ticket = data.tickets(SUPPORT, limit=1)[0]
    result = sla_status(data, SUPPORT, ticket_id=ticket["ticket_id"], target_minutes=30)
    assert not result["needs_human_confirmation"]
    assert result["caveat"] is None


# -- scoping --------------------------------------------------------------------


def test_a_customer_cannot_calculate_on_another_accounts_order(
    data: OperationalData,
) -> None:
    other = _an_order(data, account_id="ACCT-002")
    intruder = CallerContext(role=Role.CUSTOMER, account_id="ACCT-001")

    with pytest.raises(CalculationError, match="another account"):
        cancellation_timing(
            data, intruder, order_id=other["order_id"], free_window_minutes=30
        )
    with pytest.raises(CalculationError, match="another account"):
        pickup_delay(data, intruder, order_id=other["order_id"], threshold_hours=2)


def test_a_customer_cannot_calculate_on_another_accounts_ticket(
    data: OperationalData,
) -> None:
    other = data.tickets(SUPPORT, account_id="ACCT-002", limit=1)[0]
    intruder = CallerContext(role=Role.CUSTOMER, account_id="ACCT-001")

    with pytest.raises(CalculationError, match="another account"):
        sla_status(data, intruder, ticket_id=other["ticket_id"], target_minutes=30)


def test_an_unknown_record_is_refused_without_confirming_it_exists(
    data: OperationalData,
) -> None:
    """The message must read the same whether the record is missing or off-limits."""
    with pytest.raises(CalculationError) as missing:
        cancellation_timing(data, SUPPORT, order_id="ORD-NOPE", free_window_minutes=30)
    assert "may not exist" in str(missing.value)

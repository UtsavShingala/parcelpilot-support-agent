"""Deterministic arithmetic over operational rows.

Two rules keep these functions honest.

**No policy numbers live here.** Thresholds, fee amounts and credit formulas arrive
as arguments, read by the agent out of the documents it retrieved. A calculator
that knew the cancellation fee was INR 250 would be hard-coding an answer the brief
says will be tested with different records -- and would keep returning 250 after
the SOP changed. Passing them in also makes the reasoning auditable: every result
reports the inputs it used, so a wrong answer can be traced to a misread clause
rather than to arithmetic.

**Time is measured from the dataset snapshot.** Never the wall clock. See
:mod:`parcelpilot.data.database`.

Business-hours targets are the one thing deliberately *not* computed. The support
policy states several targets in business hours, and the pack records no working
calendar or holiday list. Rather than quietly treating them as elapsed hours, these
functions compute the wall-clock figure and mark the result as needing human
confirmation -- which is a genuine escalation trigger, not a gap.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from parcelpilot.auth.context import CallerContext
from parcelpilot.data.queries import OperationalData

BUSINESS_HOURS_CAVEAT = (
    "This target is stated in business hours. The dataset records no working "
    "calendar, so the elapsed figure below is wall-clock time and may overstate "
    "the breach. A human should confirm before acting on it."
)


class CalculationError(ValueError):
    """A calculation could not be performed on the data available to this caller."""


def cancellation_timing(
    data: OperationalData,
    caller: CallerContext,
    *,
    order_id: str,
    free_window_minutes: float,
) -> dict[str, Any]:
    """How the order stands against a free-cancellation window of the given length.

    The window is measured to the moment cancellation was *requested* where the
    order records one, not to the snapshot: a customer who asked in time should not
    lose the waiver because the request sat unanswered.
    """
    order = _require_order(data, caller, order_id)
    booked_at = _timestamp(order, "booked_at", order_id)
    requested_at = _optional_timestamp(order, "cancellation_requested_at")

    measured_to = requested_at or data.snapshot_at
    elapsed = _minutes_between(booked_at, measured_to)

    return {
        "order_id": order_id,
        "order_status": order.get("status"),
        "booked_at": booked_at.isoformat(),
        "cancellation_requested_at": requested_at.isoformat() if requested_at else None,
        "measured_to": measured_to.isoformat(),
        "measured_to_basis": "cancellation request" if requested_at else "dataset snapshot",
        "reference_time": data.snapshot_at.isoformat(),
        "minutes_since_booking": round(elapsed, 1),
        "free_window_minutes": free_window_minutes,
        "within_free_window": elapsed <= free_window_minutes,
        "shipment_fee_inr": order.get("shipment_fee_inr"),
    }


def pickup_delay(
    data: OperationalData,
    caller: CallerContext,
    *,
    order_id: str,
    threshold_hours: float,
) -> dict[str, Any]:
    """How late the pickup ran against the end of its scheduled window."""
    order = _require_order(data, caller, order_id)
    window_end = _timestamp(order, "pickup_window_end", order_id)
    actual = _optional_timestamp(order, "pickup_actual_at")

    measured_to = actual or data.snapshot_at
    hours_late = max(_minutes_between(window_end, measured_to) / 60, 0.0)

    return {
        "order_id": order_id,
        "order_status": order.get("status"),
        "pickup_window_end": window_end.isoformat(),
        "pickup_actual_at": actual.isoformat() if actual else None,
        "awaiting_pickup": actual is None,
        "measured_to": measured_to.isoformat(),
        "measured_to_basis": "actual pickup" if actual else "dataset snapshot",
        "reference_time": data.snapshot_at.isoformat(),
        "hours_late": round(hours_late, 2),
        "threshold_hours": threshold_hours,
        "exceeds_threshold": hours_late > threshold_hours,
        "carrier_fault": _as_bool(order.get("carrier_fault")),
        "customer_fault": _as_bool(order.get("customer_fault")),
        "shipment_fee_inr": order.get("shipment_fee_inr"),
    }


def service_credit_amount(
    *,
    shipment_fee_inr: float | None = None,
    flat_amount_inr: float | None = None,
    percentage_of_fee: float | None = None,
    maximum_inr: float | None = None,
    monthly_cap_inr: float | None = None,
    credits_already_issued_inr: float = 0.0,
    approval_threshold_inr: float | None = None,
) -> dict[str, Any]:
    """Work out a credit from whichever formula the governing document states.

    Where both a flat amount and a percentage are supplied the lower wins, which is
    how "the lower of INR 500 or 10% of the shipment fee" is expressed. Supplying
    only one applies only that one -- a contract naming a fixed credit replaces the
    formula rather than competing with it.
    """
    candidates: dict[str, float] = {}
    if flat_amount_inr is not None:
        candidates["flat amount"] = float(flat_amount_inr)
    if percentage_of_fee is not None:
        if shipment_fee_inr is None:
            raise CalculationError(
                "a percentage credit needs the shipment fee; look up the order first"
            )
        candidates["percentage of fee"] = float(shipment_fee_inr) * float(percentage_of_fee) / 100

    if not candidates:
        raise CalculationError(
            "no credit formula supplied; give a flat amount, a percentage, or both"
        )

    basis = min(candidates, key=lambda name: candidates[name])
    amount = candidates[basis]
    notes: list[str] = []

    if maximum_inr is not None and amount > maximum_inr:
        amount = float(maximum_inr)
        notes.append(f"reduced to the per-credit maximum of INR {maximum_inr:,.0f}")

    remaining: float | None = None
    if monthly_cap_inr is not None:
        remaining = max(float(monthly_cap_inr) - float(credits_already_issued_inr), 0.0)
        if amount > remaining:
            amount = remaining
            notes.append(f"reduced to INR {remaining:,.0f} remaining under the monthly cap")

    needs_approval = approval_threshold_inr is not None and amount > approval_threshold_inr
    if needs_approval:
        notes.append(
            f"exceeds the INR {approval_threshold_inr:,.0f} approval threshold; "
            "a manager must approve"
        )

    return {
        "credit_inr": round(amount, 2),
        "chosen_basis": basis,
        "candidates_inr": {name: round(value, 2) for name, value in candidates.items()},
        "shipment_fee_inr": shipment_fee_inr,
        "monthly_cap_inr": monthly_cap_inr,
        "credits_already_issued_inr": credits_already_issued_inr,
        "remaining_under_cap_inr": None if remaining is None else round(remaining, 2),
        "requires_manager_approval": needs_approval,
        "notes": notes,
    }


def sla_status(
    data: OperationalData,
    caller: CallerContext,
    *,
    ticket_id: str,
    target_minutes: float,
    target_is_business_hours: bool = False,
) -> dict[str, Any]:
    """Elapsed time on a ticket against a first-response target."""
    ticket = _require_ticket(data, caller, ticket_id)
    created_at = _timestamp(ticket, "created_at", ticket_id)
    elapsed = _minutes_between(created_at, data.snapshot_at)
    breached = elapsed > target_minutes

    return {
        "ticket_id": ticket_id,
        "ticket_status": ticket.get("status"),
        "subject": ticket.get("subject"),
        "created_at": created_at.isoformat(),
        "reference_time": data.snapshot_at.isoformat(),
        "elapsed_minutes": round(elapsed, 1),
        "target_minutes": target_minutes,
        "breached": breached,
        "minutes_over_target": round(elapsed - target_minutes, 1) if breached else 0.0,
        "minutes_remaining": 0.0 if breached else round(target_minutes - elapsed, 1),
        "target_is_business_hours": target_is_business_hours,
        "needs_human_confirmation": target_is_business_hours,
        "caveat": BUSINESS_HOURS_CAVEAT if target_is_business_hours else None,
    }


def _require_order(
    data: OperationalData, caller: CallerContext, order_id: str
) -> dict[str, Any]:
    order = data.order(caller, order_id)
    if order is None:
        raise CalculationError(
            f"no order {order_id} is visible to this caller; it may not exist, or it "
            "may belong to another account"
        )
    return order


def _require_ticket(
    data: OperationalData, caller: CallerContext, ticket_id: str
) -> dict[str, Any]:
    ticket = data.ticket(caller, ticket_id)
    if ticket is None:
        raise CalculationError(
            f"no ticket {ticket_id} is visible to this caller; it may not exist, or it "
            "may belong to another account"
        )
    return ticket


def _timestamp(row: dict[str, Any], column: str, record_id: str) -> datetime:
    value = row.get(column)
    if not value:
        raise CalculationError(f"{record_id} records no {column}, so this cannot be calculated")
    return datetime.fromisoformat(str(value))


def _optional_timestamp(row: dict[str, Any], column: str) -> datetime | None:
    value = row.get(column)
    return datetime.fromisoformat(str(value)) if value else None


def _minutes_between(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 60


def _as_bool(value: Any) -> bool:
    return bool(value) and str(value).lower() not in {"0", "false", "no", ""}

"""Lookups over accounts, orders and tickets.

These are thin wrappers over :mod:`parcelpilot.data.queries`. The scoping they rely
on happens in SQL, one layer down -- nothing here filters rows, because a tool that
could forget to filter is a tool that eventually will.

Every result reports the scope it was read under. That is partly for the model, so
an empty result reads as "not in your account" rather than "does not exist", and
partly for the transcript: a reviewer watching tool calls can see the boundary being
enforced instead of taking it on trust.
"""

from __future__ import annotations

from typing import Any

from parcelpilot.agent.tools.base import (
    ALL_ROLES,
    Tool,
    integer_field,
    object_schema,
    string_field,
)
from parcelpilot.auth.context import CallerContext
from parcelpilot.data.queries import OperationalData

ORDERS_DESCRIPTION = """\
Look up shipment orders. Returns booking and pickup timestamps, status, carrier,
shipment fee, and whether carrier or customer fault was recorded.

Call this before answering anything about a specific shipment. Timestamps here are
what the cancellation and service-credit calculations run on -- do not estimate
elapsed time yourself, use the calculate tool.

You only ever see orders the signed-in user is entitled to. An empty result means
no such order is visible to them, which is not the same as no such order existing.\
"""

TICKETS_DESCRIPTION = """\
Look up support tickets: subject, description, status, assignment, and timestamps.

Some closed tickets carry a `historical_resolution` -- what an agent told the
customer at the time. These are NOT policy and the dataset states that some of them
are wrong. Treat them as background on what was said before, never as a source for
what the rule is. Check current policy with search_documents before repeating one.

You only ever see tickets the signed-in user is entitled to.\
"""

ACCOUNT_DESCRIPTION = """\
Look up account details: plan, status, assigned CSM, and whether a signed agreement
exists for the account.

The plan decides which default support targets apply, so look this up before
answering an SLA question. If the account has a contract, search_documents will
return its terms, and those outrank the general policy.\
"""


def build_lookup_orders(data: OperationalData) -> Tool:
    def lookup_orders(
        caller: CallerContext,
        *,
        order_id: str | None = None,
        account_id: str | None = None,
        status: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        rows = data.orders(
            caller, order_id=order_id, account_id=account_id, status=status, limit=limit
        )
        return _envelope(caller, rows, "orders")

    return Tool(
        name="lookup_orders",
        description=ORDERS_DESCRIPTION,
        parameters=object_schema(
            {
                "order_id": string_field("A specific order id, such as ORD-1001."),
                "account_id": string_field(
                    "Restrict to one account. Internal users only; a customer is "
                    "confined to their own account regardless."
                ),
                "status": string_field(
                    "Filter by shipment status.",
                    enum=["DRAFT", "BOOKED", "PICKED_UP", "DELIVERED", "CANCELLED"],
                ),
                "limit": integer_field("How many orders to return.", minimum=1, maximum=50),
            }
        ),
        handler=lookup_orders,
        roles=ALL_ROLES,
    )


def build_lookup_tickets(data: OperationalData) -> Tool:
    def lookup_tickets(
        caller: CallerContext,
        *,
        ticket_id: str | None = None,
        account_id: str | None = None,
        status: str | None = None,
        assigned_to: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        rows = data.tickets(
            caller,
            ticket_id=ticket_id,
            account_id=account_id,
            status=status,
            assigned_to=assigned_to,
            limit=limit,
        )
        return _envelope(caller, rows, "tickets")

    return Tool(
        name="lookup_tickets",
        description=TICKETS_DESCRIPTION,
        parameters=object_schema(
            {
                "ticket_id": string_field("A specific ticket id, such as TKT-501."),
                "account_id": string_field("Restrict to one account. Internal users only."),
                "status": string_field("Filter by ticket status, such as open or closed."),
                "assigned_to": string_field("Filter by the agent a ticket is assigned to."),
                "limit": integer_field("How many tickets to return.", minimum=1, maximum=50),
            }
        ),
        handler=lookup_tickets,
        roles=ALL_ROLES,
    )


def build_lookup_account(data: OperationalData) -> Tool:
    def lookup_account(
        caller: CallerContext, *, account_id: str | None = None
    ) -> dict[str, Any]:
        rows = (
            [row for row in [data.account(caller, account_id)] if row]
            if account_id
            else data.accounts(caller)
        )
        return _envelope(caller, rows, "accounts")

    return Tool(
        name="lookup_account",
        description=ACCOUNT_DESCRIPTION,
        parameters=object_schema(
            {
                "account_id": string_field(
                    "A specific account id. Omit to list every account you may see."
                )
            }
        ),
        handler=lookup_account,
        roles=ALL_ROLES,
    )


def build_operational_tools(data: OperationalData) -> list[Tool]:
    return [build_lookup_orders(data), build_lookup_tickets(data), build_lookup_account(data)]


def _envelope(
    caller: CallerContext, rows: list[dict[str, Any]], noun: str
) -> dict[str, Any]:
    return {
        "visible_scope": caller.account_scope().describe(),
        "result_count": len(rows),
        noun: rows,
        "note": (
            f"No {noun} visible to this user matched. This does not mean none exist."
            if not rows
            else None
        ),
    }


__all__ = [
    "build_lookup_account",
    "build_lookup_orders",
    "build_lookup_tickets",
    "build_operational_tools",
]

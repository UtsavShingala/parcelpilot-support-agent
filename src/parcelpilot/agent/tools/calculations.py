"""The calculate tool.

One tool with an ``operation`` discriminator rather than four separate ones. The
four calculations share the same shape -- read a record, compare it against a
threshold the agent found in a document -- and keeping the tool surface small makes
the choice between tools easier for a model to get right than a menu of near
neighbours would.

The cost is a schema where which arguments are required depends on the operation.
That is validated in the handler, and a missing argument comes back as a message
naming what it needs, so the model can correct itself in one turn.
"""

from __future__ import annotations

from typing import Any

from parcelpilot.agent.calculate import (
    CalculationError,
    cancellation_timing,
    pickup_delay,
    service_credit_amount,
    sla_status,
)
from parcelpilot.agent.tools.base import (
    ALL_ROLES,
    Tool,
    ToolError,
    boolean_field,
    number_field,
    object_schema,
    string_field,
)
from parcelpilot.auth.context import CallerContext
from parcelpilot.data.queries import OperationalData

OPERATIONS = ("cancellation_timing", "pickup_delay", "service_credit", "sla_status")

DESCRIPTION = """\
Run a calculation over order or ticket data, against a threshold you have read from
a document. Never do this arithmetic yourself and never guess a threshold.

All timing is measured from the dataset snapshot, not today's date.

Operations:

- cancellation_timing: how long after booking cancellation was requested, against a
  free-cancellation window. Needs order_id and free_window_minutes.
- pickup_delay: how late a pickup ran past its window, and whether fault was
  recorded. Needs order_id and threshold_hours.
- service_credit: what credit a formula produces. Supply flat_amount_inr,
  percentage_of_fee, or both -- with both, the lower wins, which is how "the lower
  of INR X or Y% of the fee" is expressed. Supply only the one a contract names if
  the contract replaces the formula. Add monthly_cap_inr and
  credits_already_issued_inr where a cap applies, and approval_threshold_inr where
  approval is required above a level.
- sla_status: elapsed time on a ticket against a first-response target. Needs
  ticket_id and target_minutes. Set target_is_business_hours when the policy states
  the target in business hours -- the result is then flagged as needing human
  confirmation, because the dataset holds no working calendar.\
"""


def build_calculate(data: OperationalData) -> Tool:
    def calculate(
        caller: CallerContext,
        *,
        operation: str,
        order_id: str | None = None,
        ticket_id: str | None = None,
        free_window_minutes: float | None = None,
        threshold_hours: float | None = None,
        target_minutes: float | None = None,
        target_is_business_hours: bool = False,
        shipment_fee_inr: float | None = None,
        flat_amount_inr: float | None = None,
        percentage_of_fee: float | None = None,
        maximum_inr: float | None = None,
        monthly_cap_inr: float | None = None,
        credits_already_issued_inr: float = 0.0,
        approval_threshold_inr: float | None = None,
    ) -> dict[str, Any]:
        try:
            if operation == "cancellation_timing":
                _require(order_id=order_id, free_window_minutes=free_window_minutes)
                result = cancellation_timing(
                    data,
                    caller,
                    order_id=str(order_id),
                    free_window_minutes=float(free_window_minutes),  # type: ignore[arg-type]
                )
            elif operation == "pickup_delay":
                _require(order_id=order_id, threshold_hours=threshold_hours)
                result = pickup_delay(
                    data,
                    caller,
                    order_id=str(order_id),
                    threshold_hours=float(threshold_hours),  # type: ignore[arg-type]
                )
            elif operation == "service_credit":
                result = service_credit_amount(
                    shipment_fee_inr=shipment_fee_inr,
                    flat_amount_inr=flat_amount_inr,
                    percentage_of_fee=percentage_of_fee,
                    maximum_inr=maximum_inr,
                    monthly_cap_inr=monthly_cap_inr,
                    credits_already_issued_inr=credits_already_issued_inr,
                    approval_threshold_inr=approval_threshold_inr,
                )
            elif operation == "sla_status":
                _require(ticket_id=ticket_id, target_minutes=target_minutes)
                result = sla_status(
                    data,
                    caller,
                    ticket_id=str(ticket_id),
                    target_minutes=float(target_minutes),  # type: ignore[arg-type]
                    target_is_business_hours=target_is_business_hours,
                )
            else:
                raise ToolError(
                    f"unknown operation {operation!r}; expected one of {', '.join(OPERATIONS)}"
                )
        except CalculationError as error:
            raise ToolError(str(error)) from error

        return {"operation": operation, **result}

    return Tool(
        name="calculate",
        description=DESCRIPTION,
        parameters=object_schema(
            {
                "operation": string_field("Which calculation to run.", enum=list(OPERATIONS)),
                "order_id": string_field("Order id, for cancellation_timing and pickup_delay."),
                "ticket_id": string_field("Ticket id, for sla_status."),
                "free_window_minutes": number_field(
                    "Length of the free-cancellation window in minutes, from the SOP or "
                    "the customer's agreement."
                ),
                "threshold_hours": number_field(
                    "Delay in hours beyond which a credit becomes due, from the governing "
                    "document."
                ),
                "target_minutes": number_field(
                    "First-response target in minutes, from the plan's policy targets or "
                    "the customer's agreement."
                ),
                "target_is_business_hours": boolean_field(
                    "True when the policy states the target in business hours rather than "
                    "elapsed hours."
                ),
                "shipment_fee_inr": number_field(
                    "Shipment fee, needed when the credit is a percentage of it."
                ),
                "flat_amount_inr": number_field("A fixed credit amount named by a document."),
                "percentage_of_fee": number_field(
                    "Credit as a percentage of the shipment fee, for example 10."
                ),
                "maximum_inr": number_field("A per-credit maximum, if the document sets one."),
                "monthly_cap_inr": number_field(
                    "A monthly aggregate cap on credits, if the agreement sets one."
                ),
                "credits_already_issued_inr": number_field(
                    "Credits already issued this month, when a monthly cap applies."
                ),
                "approval_threshold_inr": number_field(
                    "Amount above which manager approval is required, from the SOP."
                ),
            },
            required=["operation"],
        ),
        handler=calculate,
        roles=ALL_ROLES,
    )


def _require(**arguments: Any) -> None:
    missing = [name for name, value in arguments.items() if value is None]
    if missing:
        raise ToolError(
            f"this operation needs {', '.join(missing)}; look it up or read it from the "
            "governing document first"
        )

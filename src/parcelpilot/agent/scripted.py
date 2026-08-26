"""A deterministic stand-in for a model, so the system can be shown without a key.

This is not a mock in the testing sense and it is not hidden behind a test flag. It
is a first-class mode -- ``SCRIPTED=1`` or ``--scripted`` -- because a reviewer with
no credentials should still be able to watch the pipeline work, and because a demo
that cannot run when a provider is down or a budget is spent is a demo that will
eventually fail at the worst moment.

What makes it honest rather than a puppet show: **nothing it says is written here.**
It picks a plan from the question, runs real tools through the real registry under
the real caller scope, reads the threshold out of the retrieved document text, and
composes its answer from what came back. If account scoping is broken, the scripted
answer is wrong in exactly the way the real one would be. What it does *not*
demonstrate is a model's judgement about which tools to reach for -- that is the one
thing a key buys, and the interface labels this mode so nobody mistakes the two.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from parcelpilot.agent.model import Message, ModelReply, ToolCall

ORDER_ID = re.compile(r"\b[A-Z]{2,}-\d+\b")
TICKET_ID = re.compile(r"\bTKT-\d+\b", re.IGNORECASE)

# "No fee within 30 minutes of booking" -- the free-cancellation window, read from
# whichever document the search returned rather than assumed.
FREE_WINDOW = re.compile(r"within\s+(\d+)\s*minutes", re.IGNORECASE)
# "more than 2 hours past the end of the scheduled pickup window"
DELAY_THRESHOLD = re.compile(r"more than\s+(\d+)\s*hours", re.IGNORECASE)


@dataclass(frozen=True)
class Plan:
    """A sequence of tool calls, chosen by what the question is about."""

    name: str
    triggers: tuple[str, ...]
    search_query: str


PLANS: tuple[Plan, ...] = (
    # First, because someone asking for a person has said what they want plainly and
    # no amount of retrieval should talk them out of it.
    Plan(
        name="escalation",
        triggers=(
            "escalate",
            "escalation",
            "speak to a human",
            "talk to a human",
            "to a human",
            "real person",
            "raise this",
        ),
        search_query="escalation when a request needs human judgment",
    ),
    Plan(
        name="cancellation",
        triggers=("cancel", "cancellation", "cancelling", "call off"),
        search_query="cancellation fee booked shipment before pickup",
    ),
    Plan(
        name="service_credit",
        triggers=("credit", "refund", "compensat", "late pickup", "missed pickup"),
        search_query="failed pickup service credit carrier fault",
    ),
    Plan(
        name="sla",
        triggers=("sla", "response target", "first response", "how long", "breach"),
        search_query="first response target severity plan",
    ),
    Plan(
        name="general",
        triggers=(),
        search_query="",
    ),
)


class ScriptedModelClient:
    """Replays a fixed plan of tool calls, then answers from their results."""

    name = "scripted"

    def reply(
        self, *, messages: Sequence[Message], tools: Sequence[dict[str, Any]]
    ) -> ModelReply:
        question = _last_question(messages)
        results = _tool_results(messages)
        plan = _choose_plan(question)
        available = {tool["function"]["name"] for tool in tools}

        call = _next_call(plan, question, results, available)
        if call is not None:
            return ModelReply(tool_calls=(call,))
        return ModelReply(text=_compose(plan, results))


def _last_question(messages: Sequence[Message]) -> str:
    for message in reversed(messages):
        if message.role == "user":
            return message.content
    return ""


def _tool_results(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Every tool payload so far, in order, tagged with the tool that produced it."""
    names: dict[str, str] = {}
    for message in messages:
        for call in message.tool_calls:
            names[call.call_id] = call.name

    payloads: list[dict[str, Any]] = []
    for message in messages:
        if message.role != "tool" or message.tool_call_id is None:
            continue
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append({"tool": names.get(message.tool_call_id, "?"), "payload": payload})
    return payloads


def _choose_plan(question: str) -> Plan:
    lowered = question.lower()
    for plan in PLANS:
        if any(trigger in lowered for trigger in plan.triggers):
            return plan
    return PLANS[-1]


def _seen(results: list[dict[str, Any]], tool: str) -> dict[str, Any] | None:
    for entry in results:
        if entry["tool"] == tool:
            return entry["payload"]
    return None


def _next_call(
    plan: Plan, question: str, results: list[dict[str, Any]], available: set[str]
) -> ToolCall | None:
    """The next step, or None when there is nothing left to look up."""
    step = len(results)
    record_id = _record_id(question)

    if plan.name == "general":
        if step == 0 and "search_documents" in available:
            return _call("search_documents", query=question or "support policy")
        return None

    if step == 0 and record_id and "lookup_orders" in available and plan.name != "sla":
        return _call("lookup_orders", order_id=record_id)

    if _seen(results, "search_documents") is None and "search_documents" in available:
        return _call("search_documents", query=plan.search_query, limit=6)

    if plan.name == "escalation":
        # Drafted, never performed. The draft is what a person is then asked to
        # confirm, which is the same two-phase path a live model takes.
        if "prepare_escalation" in available and _seen(results, "prepare_escalation") is None:
            return _call(
                "prepare_escalation",
                reason=(
                    f"The customer asked for a person: {question.strip()}"
                    if question.strip()
                    else "The customer asked for a person."
                ),
                severity="P3",
                **({"order_id": record_id} if record_id else {}),
            )
        return None

    if "calculate" in available and _seen(results, "calculate") is None:
        return _calculation(plan, results, record_id)

    return None


def _calculation(
    plan: Plan, results: list[dict[str, Any]], record_id: str | None
) -> ToolCall | None:
    """Build a calculation from a threshold read out of the retrieved documents."""
    documents = _seen(results, "search_documents") or {}
    corpus_text = " ".join(item.get("text", "") for item in documents.get("results", []))
    order = _first_order(results)

    if plan.name == "cancellation" and order:
        window = FREE_WINDOW.search(corpus_text)
        if window:
            return _call(
                "calculate",
                operation="cancellation_timing",
                order_id=order["order_id"],
                free_window_minutes=float(window.group(1)),
            )

    if plan.name == "service_credit" and order:
        threshold = DELAY_THRESHOLD.search(corpus_text)
        if threshold:
            return _call(
                "calculate",
                operation="pickup_delay",
                order_id=order["order_id"],
                threshold_hours=float(threshold.group(1)),
            )

    return None


def _first_order(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    payload = _seen(results, "lookup_orders") or {}
    orders = payload.get("orders") or []
    return orders[0] if orders else None


def _record_id(question: str) -> str | None:
    match = ORDER_ID.search(question)
    return match.group(0) if match else None


def _call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(call_id=f"scripted-{name}-{len(arguments)}", name=name, arguments=arguments)


# -- answer composition ---------------------------------------------------------


@dataclass
class _Findings:
    citations: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    governing: str | None = None
    orders: list[dict[str, Any]] = field(default_factory=list)
    calculation: dict[str, Any] | None = None
    scope: str = ""


def _gather(results: list[dict[str, Any]]) -> _Findings:
    findings = _Findings()
    for entry in results:
        payload = entry["payload"]
        findings.scope = payload.get("visible_scope") or findings.scope
        for item in payload.get("results", []):
            findings.citations.append(item["citation"])
        for conflict in payload.get("conflicts", []):
            findings.conflicts.append(conflict["explanation"])
            findings.governing = findings.governing or conflict["governing"]
        findings.orders += payload.get("orders", [])
        if entry["tool"] == "calculate":
            findings.calculation = payload
    return findings


def _compose(plan: Plan, results: list[dict[str, Any]]) -> str:
    """Write the answer out of what the tools actually returned."""
    findings = _gather(results)
    if not findings.citations:
        return (
            "I could not find anything in ParcelPilot's documents that answers this. "
            "Rather than guess, I would hand this to a support agent."
        )

    lines: list[str] = []

    if findings.conflicts:
        lines.append(findings.conflicts[0])
    else:
        lines.append(f"The governing source here is {findings.citations[0]}.")

    if findings.orders:
        order = findings.orders[0]
        lines.append(
            f"{order['order_id']} is {order['status']}, booked {order['booked_at']}, "
            f"fee INR {order.get('shipment_fee_inr')}."
        )

    calculation = findings.calculation
    if calculation:
        lines.append(_explain_calculation(calculation))

    lines.append("Sources: " + "; ".join(dict.fromkeys(findings.citations[:3])) + ".")
    if findings.scope:
        lines.append(f"(Answered within {findings.scope}.)")
    return " ".join(lines)


def _explain_calculation(calculation: dict[str, Any]) -> str:
    operation = calculation.get("operation")

    if operation == "cancellation_timing":
        elapsed = calculation["minutes_since_booking"]
        window = calculation["free_window_minutes"]
        inside = calculation["within_free_window"]
        verdict = (
            "inside the free-cancellation window"
            if inside
            else "outside the free-cancellation window under the general SOP"
        )
        return (
            f"Cancellation was raised {elapsed:.0f} minutes after booking against a "
            f"{window:.0f}-minute window, measured to the {calculation['measured_to_basis']} "
            f"and against the dataset snapshot -- {verdict}."
        )

    if operation == "pickup_delay":
        return (
            f"The pickup ran {calculation['hours_late']:.1f} hours past the window against a "
            f"{calculation['threshold_hours']:.0f}-hour threshold "
            f"(carrier fault: {calculation['carrier_fault']}, "
            f"customer fault: {calculation['customer_fault']})."
        )

    if operation == "sla_status":
        return (
            f"Elapsed {calculation['elapsed_minutes']:.0f} minutes against a "
            f"{calculation['target_minutes']:.0f}-minute target; "
            f"breached: {calculation['breached']}."
        )

    return f"Calculation {operation} returned {json.dumps(calculation, default=str)[:160]}."

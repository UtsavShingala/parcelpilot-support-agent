"""The same question, asked by different callers, must reach different material.

These run the real loop over the real corpus. The model is scripted to issue the
*identical* tool calls for every persona, so nothing here depends on the model
choosing differently -- any divergence is produced by scoping and authority in the
tool layer, which is where it should come from. What the model would then write is
the one part not proven here; that needs a key and the compare command.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from parcelpilot.agent.loop import SupportAgent, Turn, collect
from parcelpilot.agent.model import Message, ModelReply, ToolCall
from parcelpilot.agent.registry import ToolRegistry, build_registry
from parcelpilot.auth.context import CallerContext, Role
from parcelpilot.data.queries import OperationalData
from parcelpilot.retrieval.store import DocumentStore

NORTHSTAR = CallerContext(role=Role.CUSTOMER, account_id="ACCT-001", display_name="Northstar")
LUMENWORKS = CallerContext(role=Role.CUSTOMER, account_id="ACCT-002", display_name="LumenWorks")
BEACON = CallerContext(role=Role.CUSTOMER, account_id="ACCT-003", display_name="Beacon Retail")
SUPPORT = CallerContext(role=Role.SUPPORT_AGENT, display_name="Maya")


class FixedScriptClient:
    """Issues a fixed sequence of tool calls, then answers from what came back.

    The answer is derived from tool results rather than invented, so two personas
    getting different answers means they were given different material.
    """

    def __init__(self, calls: Sequence[ToolCall]) -> None:
        self._queue = list(calls)

    def reply(
        self, *, messages: Sequence[Message], tools: Sequence[dict[str, Any]]
    ) -> ModelReply:
        if self._queue:
            return ModelReply(tool_calls=(self._queue.pop(0),))
        return ModelReply(text=json.dumps(_harvest(messages), sort_keys=True))


def _harvest(messages: Sequence[Message]) -> dict[str, Any]:
    citations: list[str] = []
    conflicts: list[str] = []
    orders: list[str] = []
    for message in messages:
        if message.role != "tool":
            continue
        payload = json.loads(message.content)
        if not isinstance(payload, dict):
            continue
        citations += [item["citation"] for item in payload.get("results", [])]
        conflicts += [item["governing"] for item in payload.get("conflicts", [])]
        orders += [item["order_id"] for item in payload.get("orders", [])]
    return {"citations": citations, "conflicts": conflicts, "orders": orders}


@pytest.fixture(scope="module")
def parts(corpus_dir: Path) -> Iterator[tuple[ToolRegistry, Any]]:
    data = OperationalData.open()
    yield build_registry(DocumentStore.from_settings(), data), data.snapshot_at
    data.close()


def _ask(
    parts: tuple[ToolRegistry, Any], caller: CallerContext, calls: Sequence[ToolCall]
) -> Turn:
    registry, snapshot = parts
    agent = SupportAgent(
        registry=registry,
        client=FixedScriptClient(calls),
        snapshot_at=snapshot,
        max_steps=12,
    )
    return collect(agent.run(caller, "Can I cancel my booked shipment without a fee?"))


def _seen(turn: Turn) -> dict[str, Any]:
    return json.loads(turn.answer)


CANCELLATION_SEARCH = ToolCall(
    call_id="c1",
    name="search_documents",
    arguments={"query": "cancellation fee booked shipment before pickup", "limit": 6},
)


def test_two_customers_reach_their_own_agreement_and_never_the_other(
    parts: tuple[ToolRegistry, Any],
) -> None:
    northstar = _seen(_ask(parts, NORTHSTAR, [CANCELLATION_SEARCH]))
    lumenworks = _seen(_ask(parts, LUMENWORKS, [CANCELLATION_SEARCH]))

    northstar_citations = " ".join(northstar["citations"])
    lumenworks_citations = " ".join(lumenworks["citations"])

    assert "Northstar" in northstar_citations
    assert "LumenWorks" not in northstar_citations

    assert "LumenWorks" in lumenworks_citations
    assert "Northstar" not in lumenworks_citations


def test_identical_tool_calls_still_produce_different_answers(
    parts: tuple[ToolRegistry, Any],
) -> None:
    """The requirement, stated directly: same question, different callers, different answers."""
    answers = {
        caller.account_id: _ask(parts, caller, [CANCELLATION_SEARCH]).answer
        for caller in (NORTHSTAR, LUMENWORKS, BEACON)
    }
    assert len(set(answers.values())) == 3, "personas received identical material"


def test_a_customer_with_no_agreement_sees_only_general_policy(
    parts: tuple[ToolRegistry, Any],
) -> None:
    """Beacon Retail signed nothing, so no contract may govern for them.

    General documents can still rank against each other -- the SOP outranks the
    product guide for everyone -- so the claim is about agreements, not conflicts.
    """
    beacon = _seen(_ask(parts, BEACON, [CANCELLATION_SEARCH]))

    assert beacon["citations"], "the SOP should still be found"
    assert not any("Agreement" in citation for citation in beacon["citations"])
    assert not any("Agreement" in governing for governing in beacon["conflicts"])


def test_a_contracted_customer_is_told_which_source_governs(
    parts: tuple[ToolRegistry, Any],
) -> None:
    northstar = _seen(_ask(parts, NORTHSTAR, [CANCELLATION_SEARCH]))
    assert any("Northstar" in governing for governing in northstar["conflicts"])


def test_each_customer_sees_only_their_own_orders(parts: tuple[ToolRegistry, Any]) -> None:
    call = ToolCall(call_id="c2", name="lookup_orders", arguments={"limit": 20})

    northstar = _seen(_ask(parts, NORTHSTAR, [call]))["orders"]
    lumenworks = _seen(_ask(parts, LUMENWORKS, [call]))["orders"]

    assert northstar and lumenworks
    assert not set(northstar) & set(lumenworks)


def test_internal_staff_see_every_account(parts: tuple[ToolRegistry, Any]) -> None:
    call = ToolCall(call_id="c3", name="lookup_orders", arguments={"limit": 50})

    everyone = set(_seen(_ask(parts, SUPPORT, [call]))["orders"])
    northstar = set(_seen(_ask(parts, NORTHSTAR, [call]))["orders"])

    assert northstar < everyone, "support should see strictly more than one customer"


def test_asking_for_another_accounts_order_returns_nothing(
    parts: tuple[ToolRegistry, Any],
) -> None:
    """Naming someone else's order explicitly must not widen what a customer sees."""
    someone_elses = _seen(
        _ask(parts, SUPPORT, [ToolCall(call_id="c4", name="lookup_orders", arguments={
            "account_id": "ACCT-002", "limit": 1
        })])
    )["orders"]
    assert someone_elses

    call = ToolCall(
        call_id="c5", name="lookup_orders", arguments={"order_id": someone_elses[0]}
    )
    assert _seen(_ask(parts, NORTHSTAR, [call]))["orders"] == []


def test_a_multi_step_question_chains_lookup_into_calculation(
    parts: tuple[ToolRegistry, Any],
) -> None:
    """Order, then the governing document, then arithmetic against the snapshot."""
    turn = _ask(
        parts,
        NORTHSTAR,
        [
            ToolCall(call_id="s1", name="lookup_orders", arguments={"order_id": "ORD-1001"}),
            CANCELLATION_SEARCH,
            ToolCall(
                call_id="s3",
                name="calculate",
                arguments={
                    "operation": "cancellation_timing",
                    "order_id": "ORD-1001",
                    "free_window_minutes": 30,
                    # The window has to be attributed to the passage it was read in;
                    # the calculator refuses a figure it cannot find there.
                    "sources": [
                        "ParcelPilot Cancellation & Service Credit SOP v4 - 1. Order cancellation"
                    ],
                },
            ),
        ],
    )

    names = [event.name for event in turn.events if getattr(event, "type", "") == "tool_start"]
    assert names == ["lookup_orders", "search_documents", "calculate"]
    assert all(
        event.ok for event in turn.events if getattr(event, "type", "") == "tool_result"
    )

    calculation = next(
        event
        for event in turn.events
        if getattr(event, "type", "") == "tool_result" and event.name == "calculate"
    )
    assert calculation.payload["grounded_in"] == [
        "ParcelPilot Cancellation & Service Credit SOP v4 - 1. Order cancellation"
    ]

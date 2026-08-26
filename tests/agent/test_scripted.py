"""Scripted mode must exercise the real pipeline, not narrate a rehearsed answer."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from parcelpilot.agent.loop import SupportAgent, Turn, collect
from parcelpilot.agent.registry import build_registry
from parcelpilot.agent.scripted import ScriptedModelClient
from parcelpilot.auth.context import CallerContext, Role
from parcelpilot.data.queries import OperationalData
from parcelpilot.retrieval.store import DocumentStore

NORTHSTAR = CallerContext(role=Role.CUSTOMER, account_id="ACCT-001", display_name="Northstar")
LUMENWORKS = CallerContext(role=Role.CUSTOMER, account_id="ACCT-002", display_name="LumenWorks")

CANCELLATION = "Can I cancel ORD-1001 without a cancellation fee?"


@pytest.fixture(scope="module")
def agent(corpus_dir: Path) -> Iterator[SupportAgent]:
    data = OperationalData.open()
    yield SupportAgent(
        registry=build_registry(DocumentStore.from_settings(), data),
        client=ScriptedModelClient(),
        snapshot_at=data.snapshot_at,
        max_steps=12,
    )
    data.close()


def _tools(turn: Turn) -> list[str]:
    return [e.name for e in turn.events if getattr(e, "type", "") == "tool_start"]


def _arguments(turn: Turn, name: str) -> dict[str, Any]:
    for event in turn.events:
        if getattr(event, "type", "") == "tool_start" and event.name == name:
            return event.arguments
    raise AssertionError(f"{name} was never called")


def test_a_cancellation_question_runs_lookup_search_then_calculate(
    agent: SupportAgent,
) -> None:
    turn = collect(agent.run(NORTHSTAR, CANCELLATION))
    assert _tools(turn) == ["lookup_orders", "search_documents", "calculate"]


def test_the_threshold_is_read_from_the_document_not_hardcoded(
    agent: SupportAgent, corpus_dir: Path
) -> None:
    """The demo would prove nothing if the window were baked into the script."""
    turn = collect(agent.run(NORTHSTAR, CANCELLATION))
    window = _arguments(turn, "calculate")["free_window_minutes"]

    store = DocumentStore.from_settings()
    sop = " ".join(
        hit.chunk.text
        for hit in store.search(
            "cancellation fee booked shipment before pickup",
            scope=NORTHSTAR.account_scope(),
            limit=6,
        )
    )
    assert f"{int(window)} minutes" in sop, "the window did not come from the corpus"


def test_the_answer_is_built_from_what_the_tools_returned(agent: SupportAgent) -> None:
    turn = collect(agent.run(NORTHSTAR, CANCELLATION))

    assert "Northstar" in turn.answer, "the governing agreement should be named"
    assert "SOP" in turn.answer, "the overridden policy should be cited too"
    assert "Sources:" in turn.answer
    assert "ACCT-001" in turn.answer


def test_another_accounts_order_yields_nothing_and_no_calculation(
    agent: SupportAgent,
) -> None:
    """ORD-1001 belongs to ACCT-001; the scripted answer must reflect that, not paper over it."""
    turn = collect(agent.run(LUMENWORKS, CANCELLATION))

    assert "lookup_orders" in _tools(turn)
    assert "calculate" not in _tools(turn), "no order means nothing to calculate"
    assert "ORD-1001" not in turn.answer
    assert "LumenWorks" in turn.answer


def test_two_customers_get_different_answers_to_one_question(agent: SupportAgent) -> None:
    northstar = collect(agent.run(NORTHSTAR, CANCELLATION)).answer
    lumenworks = collect(agent.run(LUMENWORKS, CANCELLATION)).answer
    assert northstar != lumenworks


def test_a_service_credit_question_reaches_the_delay_calculation(
    agent: SupportAgent,
) -> None:
    turn = collect(
        agent.run(LUMENWORKS, "ORD-2002 was picked up late. Am I owed a service credit?")
    )
    assert _tools(turn) == ["lookup_orders", "search_documents", "calculate"]
    assert _arguments(turn, "calculate")["operation"] == "pickup_delay"


def test_an_unrelated_question_searches_once_and_stops(agent: SupportAgent) -> None:
    turn = collect(agent.run(NORTHSTAR, "What are your office opening hours?"))
    assert _tools(turn) == ["search_documents"]


def test_a_question_the_corpus_cannot_answer_offers_a_handover(
    agent: SupportAgent,
) -> None:
    turn = collect(agent.run(NORTHSTAR, "zzzz quantum chromodynamics zzzz"))
    assert "could not find" in turn.answer
    assert "support agent" in turn.answer


def test_the_client_is_named_so_the_interface_can_label_the_mode() -> None:
    assert ScriptedModelClient().name == "scripted"

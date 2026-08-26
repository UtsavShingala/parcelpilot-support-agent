"""Scripted mode must exercise the real pipeline and never fake the reasoning."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from parcelpilot.agent.loop import SupportAgent, Turn, collect
from parcelpilot.agent.registry import build_registry
from parcelpilot.agent.scripted import SCRIPTED_NOTICE, ScriptedModelClient, choose_plan
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


# -- plan selection -------------------------------------------------------------


def test_a_specific_intent_beats_the_general_fallback() -> None:
    assert choose_plan("can I cancel this shipment?").name == "cancellation"
    assert choose_plan("am I owed a credit for a late pickup?").name == "service_credit"
    assert choose_plan("what is the P1 first response target?").name == "sla"
    assert choose_plan("bulk upload keeps failing").name == "known_issue"
    assert choose_plan("what colour is the van?").name == "general"


# -- the demo path --------------------------------------------------------------


def test_a_cancellation_question_looks_up_the_order_then_the_documents(
    agent: SupportAgent,
) -> None:
    assert _tools(collect(agent.run(NORTHSTAR, CANCELLATION))) == [
        "lookup_orders",
        "search_documents",
    ]


def test_the_answer_names_the_mode_it_is_running_in(agent: SupportAgent) -> None:
    """A reviewer must never mistake an assembled answer for a reasoned one."""
    assert collect(agent.run(NORTHSTAR, CANCELLATION)).answer.startswith(SCRIPTED_NOTICE)


def test_the_agreement_is_cited_above_the_sop(agent: SupportAgent) -> None:
    answer = collect(agent.run(NORTHSTAR, CANCELLATION)).answer

    assert "do not agree" in answer, "the conflict must be stated, not resolved silently"
    governing = answer.split("The governing source for this question is ")[1]
    assert "Northstar" in governing.split("\n")[0]
    assert "SOP v4" in answer, "the overridden policy must still be cited"


def test_sources_are_labelled_with_tier_and_reach(agent: SupportAgent) -> None:
    answer = collect(agent.run(NORTHSTAR, CANCELLATION)).answer

    assert "[AGREEMENT, applies to your account only]" in answer
    assert "[CURRENT_POLICY, applies to all customers]" in answer


def test_scripted_mode_never_calculates_or_interprets(agent: SupportAgent) -> None:
    """Inventing "30 minutes" would look exactly like a model that read the SOP."""
    turn = collect(agent.run(NORTHSTAR, CANCELLATION))

    assert "calculate" not in _tools(turn)
    assert "does not interpret" in turn.answer


# -- access control, visible through the answer ---------------------------------


def test_another_accounts_order_is_invisible_in_the_answer(agent: SupportAgent) -> None:
    turn = collect(agent.run(LUMENWORKS, CANCELLATION))

    assert "lookup_orders" in _tools(turn)
    assert "ORD-1001" not in turn.answer, "an order on ACCT-001 leaked to ACCT-002"
    assert "LumenWorks" in turn.answer


def test_two_customers_get_different_answers_to_one_question(agent: SupportAgent) -> None:
    northstar = collect(agent.run(NORTHSTAR, CANCELLATION)).answer
    lumenworks = collect(agent.run(LUMENWORKS, CANCELLATION)).answer

    assert northstar != lumenworks
    assert "Northstar" not in lumenworks
    assert "LumenWorks" not in northstar


# -- escalation -----------------------------------------------------------------


def test_asking_for_a_human_prepares_an_escalation_without_performing_it(
    agent: SupportAgent,
) -> None:
    turn = collect(agent.run(NORTHSTAR, "I want to escalate this to a human"))

    assert _tools(turn) == ["search_documents", "prepare_escalation"]
    assert len(turn.drafts) == 1
    assert turn.escalated
    assert "Nothing has been actioned yet" in turn.answer


# -- nothing to say -------------------------------------------------------------


def test_a_question_the_corpus_cannot_answer_hands_over(agent: SupportAgent) -> None:
    turn = collect(agent.run(NORTHSTAR, "zzzz quantum chromodynamics zzzz"))

    assert "nothing I can answer from" in turn.answer
    assert "support agent" in turn.answer

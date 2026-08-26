"""The loop must be drivable without a provider, which is the point of the adapter."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from parcelpilot.agent.events import (
    ActionDrafted,
    Completed,
    Escalated,
    Failed,
    TextDelta,
    ToolFinished,
    ToolStarted,
)
from parcelpilot.agent.loop import SupportAgent, collect
from parcelpilot.agent.model import Message, ModelReply, ModelUnavailable, ToolCall
from parcelpilot.agent.registry import ToolRegistry, build_registry
from parcelpilot.agent.tools.actions import ActionKind
from parcelpilot.auth.context import CallerContext, Role
from parcelpilot.data.queries import OperationalData
from parcelpilot.retrieval.store import DocumentStore

CUSTOMER = CallerContext(role=Role.CUSTOMER, account_id="ACCT-001", display_name="Northstar")
SUPPORT = CallerContext(role=Role.SUPPORT_AGENT, display_name="Maya")


class ScriptedClient:
    """A ModelClient that replays prepared replies and records what it was sent."""

    def __init__(self, *replies: ModelReply) -> None:
        self._replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def reply(
        self, *, messages: Sequence[Message], tools: Sequence[dict[str, Any]]
    ) -> ModelReply:
        self.calls.append({"messages": list(messages), "tools": list(tools)})
        if not self._replies:
            return ModelReply(text="done")
        return self._replies.pop(0)


class BrokenClient:
    def reply(
        self, *, messages: Sequence[Message], tools: Sequence[dict[str, Any]]
    ) -> ModelReply:
        raise ModelUnavailable("no OPENAI_API_KEY is configured")


class LoopingClient:
    """Never stops calling tools -- the case the step ceiling exists for."""

    def __init__(self) -> None:
        self.count = 0

    def reply(
        self, *, messages: Sequence[Message], tools: Sequence[dict[str, Any]]
    ) -> ModelReply:
        self.count += 1
        return ModelReply(
            tool_calls=(
                ToolCall(
                    call_id=f"call-{self.count}",
                    name="search_documents",
                    arguments={"query": "cancellation"},
                ),
            )
        )


@pytest.fixture(scope="module")
def registry(corpus_dir: Path) -> Iterator[ToolRegistry]:
    data = OperationalData.open()
    yield build_registry(DocumentStore.from_settings(), data)
    data.close()


@pytest.fixture(scope="module")
def snapshot(corpus_dir: Path) -> Any:
    data = OperationalData.open()
    moment = data.snapshot_at
    data.close()
    return moment


def _agent(registry: ToolRegistry, client: Any, snapshot: Any, steps: int = 12) -> SupportAgent:
    return SupportAgent(
        registry=registry, client=client, snapshot_at=snapshot, max_steps=steps
    )


def _call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(call_id=f"call-{name}", name=name, arguments=arguments)


# -- the basic shape ------------------------------------------------------------


def test_a_direct_answer_needs_no_tools(registry: ToolRegistry, snapshot: Any) -> None:
    client = ScriptedClient(ModelReply(text="Booked shipments can be cancelled."))
    turn = collect(_agent(registry, client, snapshot).run(CUSTOMER, "can I cancel?"))

    assert turn.answer == "Booked shipments can be cancelled."
    assert turn.steps == 1
    assert not turn.escalated
    assert not any(isinstance(event, ToolStarted) for event in turn.events)


def test_a_tool_call_is_run_and_fed_back(registry: ToolRegistry, snapshot: Any) -> None:
    client = ScriptedClient(
        ModelReply(tool_calls=(_call("search_documents", query="cancellation fee"),)),
        ModelReply(text="Your agreement waives the fee."),
    )
    turn = collect(_agent(registry, client, snapshot).run(CUSTOMER, "cancellation fee?"))

    started = [e for e in turn.events if isinstance(e, ToolStarted)]
    finished = [e for e in turn.events if isinstance(e, ToolFinished)]
    assert [e.name for e in started] == ["search_documents"]
    assert finished[0].ok
    assert "result(s)" in finished[0].summary
    assert turn.answer == "Your agreement waives the fee."

    # the tool result was appended to the conversation before the second call
    second_turn_messages = client.calls[1]["messages"]
    assert second_turn_messages[-1].role == "tool"
    assert second_turn_messages[-1].tool_call_id == "call-search_documents"


def test_interim_commentary_is_emitted_before_the_final_answer(
    registry: ToolRegistry, snapshot: Any
) -> None:
    client = ScriptedClient(
        ModelReply(
            text="Let me check your agreement.",
            tool_calls=(_call("search_documents", query="cancellation"),),
        ),
        ModelReply(text="No fee applies."),
    )
    turn = collect(_agent(registry, client, snapshot).run(CUSTOMER, "cancel?"))

    deltas = [e for e in turn.events if isinstance(e, TextDelta)]
    assert [d.text for d in deltas] == ["Let me check your agreement.", "No fee applies."]
    assert [d.final for d in deltas] == [False, True]


def test_parallel_tool_calls_all_run(registry: ToolRegistry, snapshot: Any) -> None:
    client = ScriptedClient(
        ModelReply(
            tool_calls=(
                _call("lookup_orders", order_id="ORD-1001"),
                _call("search_documents", query="cancellation fee"),
            )
        ),
        ModelReply(text="Both checked."),
    )
    turn = collect(_agent(registry, client, snapshot).run(CUSTOMER, "check both"))

    assert [e.name for e in turn.events if isinstance(e, ToolStarted)] == [
        "lookup_orders",
        "search_documents",
    ]


# -- the provider boundary ------------------------------------------------------


def test_the_system_prompt_carries_the_snapshot_not_today(
    registry: ToolRegistry, snapshot: Any
) -> None:
    client = ScriptedClient(ModelReply(text="ok"))
    collect(_agent(registry, client, snapshot).run(CUSTOMER, "hello"))

    system = client.calls[0]["messages"][0]
    assert system.role == "system"
    assert str(snapshot.year) in system.content
    assert "snapshot" in system.content.lower()


def test_a_customer_is_only_sent_tools_their_role_may_use(
    registry: ToolRegistry, snapshot: Any
) -> None:
    client = ScriptedClient(ModelReply(text="ok"))
    collect(_agent(registry, client, snapshot).run(CUSTOMER, "hello"))

    offered = {tool["function"]["name"] for tool in client.calls[0]["tools"]}
    assert "prepare_ticket_update" not in offered
    assert "prepare_escalation" in offered


def test_an_unavailable_model_fails_rather_than_pretending(
    registry: ToolRegistry, snapshot: Any
) -> None:
    turn = collect(_agent(registry, BrokenClient(), snapshot).run(CUSTOMER, "hello"))

    failures = [event for event in turn.events if isinstance(event, Failed)]
    assert failures
    assert "OPENAI_API_KEY" in failures[0].message
    assert not any(isinstance(event, Completed) for event in turn.events)


# -- tool failures are recoverable ----------------------------------------------


def test_a_failed_tool_call_is_reported_and_the_turn_continues(
    registry: ToolRegistry, snapshot: Any
) -> None:
    client = ScriptedClient(
        ModelReply(tool_calls=(_call("calculate", operation="pickup_delay"),)),
        ModelReply(text="I need the order id."),
    )
    turn = collect(_agent(registry, client, snapshot).run(SUPPORT, "how late?"))

    finished = [e for e in turn.events if isinstance(e, ToolFinished)][0]
    assert not finished.ok
    assert "order_id" in (finished.error or "")
    assert turn.answer == "I need the order id."


def test_unparseable_arguments_come_back_as_a_correctable_error(
    registry: ToolRegistry, snapshot: Any
) -> None:
    client = ScriptedClient(
        ModelReply(
            tool_calls=(
                ToolCall(call_id="c1", name="search_documents", parse_error="bad JSON"),
            )
        ),
        ModelReply(text="retried"),
    )
    turn = collect(_agent(registry, client, snapshot).run(SUPPORT, "search"))

    finished = [e for e in turn.events if isinstance(e, ToolFinished)][0]
    assert not finished.ok
    assert "JSON object" in (finished.error or "")


def test_a_tool_a_customer_may_not_use_is_refused_mid_loop(
    registry: ToolRegistry, snapshot: Any
) -> None:
    """Enforcement, not the schema list, is what stops this."""
    client = ScriptedClient(
        ModelReply(
            tool_calls=(_call("prepare_ticket_update", ticket_id="TKT-501", status="closed"),)
        ),
        ModelReply(text="I cannot do that."),
    )
    turn = collect(_agent(registry, client, snapshot).run(CUSTOMER, "close my ticket"))

    finished = [e for e in turn.events if isinstance(e, ToolFinished)][0]
    assert not finished.ok
    assert "may not use" in (finished.error or "")
    assert not turn.drafts


# -- actions and escalation -----------------------------------------------------


def test_a_prepared_action_surfaces_as_a_draft(
    registry: ToolRegistry, snapshot: Any
) -> None:
    client = ScriptedClient(
        ModelReply(
            tool_calls=(_call("prepare_follow_up", subject="chase carrier", owner="Maya"),)
        ),
        ModelReply(text="Shall I create this?"),
    )
    turn = collect(_agent(registry, client, snapshot).run(SUPPORT, "follow up"))

    drafted = [e for e in turn.events if isinstance(e, ActionDrafted)]
    assert len(drafted) == 1
    assert drafted[0].draft.kind is ActionKind.FOLLOW_UP
    assert turn.drafts[0].summary.startswith("Follow-up for Maya")
    assert not turn.escalated


def test_a_drafted_escalation_is_both_a_draft_and_an_outcome(
    registry: ToolRegistry, snapshot: Any
) -> None:
    client = ScriptedClient(
        ModelReply(
            tool_calls=(_call("prepare_escalation", reason="needs a human", severity="P2"),)
        ),
        ModelReply(text="I have prepared a handover."),
    )
    turn = collect(_agent(registry, client, snapshot).run(CUSTOMER, "I want an exception"))

    assert [e.draft.kind for e in turn.events if isinstance(e, ActionDrafted)] == [
        ActionKind.ESCALATION
    ]
    escalations = [e for e in turn.events if isinstance(e, Escalated)]
    assert len(escalations) == 1
    assert escalations[0].draft is not None
    assert turn.escalated


def test_the_step_ceiling_escalates_rather_than_retrying(
    registry: ToolRegistry, snapshot: Any
) -> None:
    client = LoopingClient()
    turn = collect(_agent(registry, client, snapshot, steps=3).run(SUPPORT, "go in circles"))

    assert client.count == 3, "the ceiling must stop the loop, not extend it"
    escalation = [e for e in turn.events if isinstance(e, Escalated)][-1]
    assert "step limit" in escalation.reason
    assert escalation.draft is not None
    assert turn.escalated
    assert turn.steps == 3
    assert "handover" in turn.answer


def test_the_ceiling_escalation_names_what_went_wrong(
    registry: ToolRegistry, snapshot: Any
) -> None:
    turn = collect(_agent(registry, LoopingClient(), snapshot, steps=2).run(SUPPORT, "loop"))
    assert "2 tool calls without reaching an answer" in turn.drafts[-1].details["reason"]


def test_completion_reports_that_the_turn_escalated(
    registry: ToolRegistry, snapshot: Any
) -> None:
    client = ScriptedClient(
        ModelReply(tool_calls=(_call("prepare_escalation", reason="judgment needed"),)),
        ModelReply(text="Confirm and a person will pick this up."),
    )
    turn = collect(_agent(registry, client, snapshot).run(CUSTOMER, "please make an exception"))

    completed = [e for e in turn.events if isinstance(e, Completed)][0]
    assert completed.escalated

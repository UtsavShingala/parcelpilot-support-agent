"""A repeated tool call must not spend a step.

Models that repeat a call tend to keep repeating it. Left alone, that turns an
answerable question into an escalation at the step ceiling -- which is what
happened to "Can I cancel ORD-1001 without a fee?" on a live model: ten steps,
duplicated lookups, then a handover for a question it had answered correctly
minutes earlier.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from parcelpilot.agent.loop import SupportAgent, collect
from parcelpilot.agent.model import Message, ModelReply, ToolCall
from parcelpilot.agent.registry import ToolRegistry
from parcelpilot.agent.tools.base import Tool, object_schema, string_field
from parcelpilot.auth.context import CallerContext, Role

CALLER = CallerContext(role=Role.SUPPORT_AGENT, display_name="Maya")
SNAPSHOT = datetime(2026, 8, 16, 11, 0, tzinfo=UTC)


class _CountingRegistry:
    """A registry with one tool that records how often it actually runs."""

    def __init__(self) -> None:
        self.runs = 0
        self._registry = ToolRegistry(
            tools=(
                Tool(
                    name="lookup_orders",
                    description="Look up orders.",
                    parameters=object_schema(
                        {"order_id": string_field("An order id.")}, required=["order_id"]
                    ),
                    handler=self._handler,
                    roles=frozenset({Role.SUPPORT_AGENT}),
                ),
            )
        )

    def _handler(self, caller: CallerContext, **arguments: Any) -> dict[str, Any]:
        self.runs += 1
        return {"result_count": 1, "orders": [{"order_id": arguments["order_id"]}]}

    def __getattr__(self, item: str) -> Any:
        return getattr(self._registry, item)


class _RepeatingClient:
    """Asks for the identical call ``times`` over, then answers."""

    def __init__(self, times: int, arguments: dict[str, Any] | None = None) -> None:
        self._left = times
        self._arguments = arguments or {"order_id": "ORD-1001"}
        self.calls_seen = 0

    def reply(
        self, *, messages: Sequence[Message], tools: Sequence[dict[str, Any]]
    ) -> ModelReply:
        self.calls_seen += 1
        if self._left:
            self._left -= 1
            return ModelReply(
                tool_calls=(
                    ToolCall(
                        call_id=f"c{self._left}",
                        name="lookup_orders",
                        arguments=dict(self._arguments),
                    ),
                )
            )
        return ModelReply(text="done")


def _run(client: Any, registry: Any, max_steps: int = 12) -> Any:
    agent = SupportAgent(
        registry=registry, client=client, snapshot_at=SNAPSHOT, max_steps=max_steps
    )
    return collect(agent.run(CALLER, "Can I cancel ORD-1001 without a fee?"))


def test_an_identical_call_runs_the_tool_only_once() -> None:
    registry = _CountingRegistry()
    turn = _run(_RepeatingClient(times=4), registry)

    assert registry.runs == 1, "the tool was executed again for a call already made"
    assert turn.answer == "done"


def test_the_repeat_is_still_reported_so_the_interface_can_show_it() -> None:
    """Hiding the repeat would make the transcript disagree with what happened."""
    registry = _CountingRegistry()
    turn = _run(_RepeatingClient(times=3), registry)

    starts = [event for event in turn.events if event.type == "tool_start"]
    results = [event for event in turn.events if event.type == "tool_result"]

    assert len(starts) == 3
    assert len(results) == 3
    assert sum("already run at step" in event.summary for event in results) == 2


def test_the_model_is_told_it_already_has_the_result() -> None:
    registry = _CountingRegistry()
    seen: list[str] = []

    class Watching(_RepeatingClient):
        def reply(self, *, messages: Sequence[Message], tools: Sequence[dict[str, Any]]):
            seen.extend(m.content for m in messages if m.role == "tool")
            return super().reply(messages=messages, tools=tools)

    _run(Watching(times=2), registry)

    assert any("Do not call it again" in content for content in seen)
    assert any("repeat_of_step" in content for content in seen)


def test_a_repeating_model_still_reaches_an_answer_within_the_ceiling() -> None:
    """The failure this guards: repeats burning every step, then escalating."""
    registry = _CountingRegistry()
    turn = _run(_RepeatingClient(times=8), registry, max_steps=12)

    assert not turn.escalated, "a repeating model exhausted the step budget"
    assert turn.answer == "done"
    assert registry.runs == 1


def test_different_arguments_are_not_treated_as_a_repeat() -> None:
    """Only an identical call is a repeat; a different order is real work."""
    registry = _CountingRegistry()

    class TwoOrders:
        def __init__(self) -> None:
            self.step = 0

        def reply(self, *, messages: Sequence[Message], tools: Sequence[dict[str, Any]]):
            self.step += 1
            if self.step == 1:
                return ModelReply(
                    tool_calls=(
                        ToolCall(
                            call_id="a", name="lookup_orders", arguments={"order_id": "ORD-1"}
                        ),
                    )
                )
            if self.step == 2:
                return ModelReply(
                    tool_calls=(
                        ToolCall(
                            call_id="b", name="lookup_orders", arguments={"order_id": "ORD-2"}
                        ),
                    )
                )
            return ModelReply(text="done")

    _run(TwoOrders(), registry)
    assert registry.runs == 2


def test_argument_order_does_not_make_a_call_look_new() -> None:
    """The same arguments written in a different order are the same call."""
    registry = _CountingRegistry()

    class Reordered:
        def __init__(self) -> None:
            self.step = 0

        def reply(self, *, messages: Sequence[Message], tools: Sequence[dict[str, Any]]):
            self.step += 1
            if self.step == 1:
                return ModelReply(
                    tool_calls=(
                        ToolCall(call_id="a", name="lookup_orders", arguments={"order_id": "X"}),
                    )
                )
            if self.step == 2:
                return ModelReply(
                    tool_calls=(
                        ToolCall(call_id="b", name="lookup_orders", arguments={"order_id": "X"}),
                    )
                )
            return ModelReply(text="done")

    _run(Reordered(), registry)
    assert registry.runs == 1


def test_one_tool_cannot_be_leaned_on_forever() -> None:
    """The other shape of looping: the same search, rephrased, over and over.

    Distinct arguments each time, so the repeat guard correctly allows them. Six
    searches for one question cost three and a half minutes on a live model and
    escalated anyway.
    """
    registry = _CountingRegistry()

    class Rephrasing:
        def __init__(self) -> None:
            self.step = 0

        def reply(self, *, messages: Sequence[Message], tools: Sequence[dict[str, Any]]):
            self.step += 1
            if self.step <= 6:
                return ModelReply(
                    tool_calls=(
                        ToolCall(
                            call_id=f"c{self.step}",
                            name="lookup_orders",
                            arguments={"order_id": f"ORD-{self.step}"},
                        ),
                    )
                )
            return ModelReply(text="done")

    turn = _run(Rephrasing(), registry)

    assert registry.runs == 3, "the per-tool budget did not stop the search"
    assert turn.answer == "done"
    refusals = [
        event
        for event in turn.events
        if event.type == "tool_result" and "already used" in event.summary
    ]
    assert len(refusals) == 3

"""The agent loop.

A turn is: ask the model, run whatever tools it asked for, feed the results back,
repeat until it answers. Two properties are load-bearing.

**Dispatch never touches the provider.** The loop talks to a :class:`ModelClient`
and to the :class:`ToolRegistry`, and neither knows about the other. Tool execution,
scoping, role checks and error handling are all provider-independent, which is what
makes the client swappable and what lets the whole loop be tested against a scripted
client with no key and no network.

**The step ceiling escalates rather than retrying.** A question still calling tools
after a dozen round-trips is not one more call away from an answer; it is going in
circles, and the honest response is to hand it to a person. Retrying would spend
more money to produce a worse version of the same failure.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from parcelpilot.agent.events import (
    ActionDrafted,
    AgentEvent,
    Completed,
    Escalated,
    Failed,
    TextDelta,
    ToolFinished,
    ToolStarted,
)
from parcelpilot.agent.model import Message, ModelClient, ModelUnavailable, ToolCall
from parcelpilot.agent.prompts import system_prompt
from parcelpilot.agent.registry import ToolCallResult, ToolRegistry
from parcelpilot.agent.tools.actions import ActionDraft, ActionKind
from parcelpilot.auth.context import CallerContext

CEILING_REASON = "the assistant could not resolve this within its step limit"

PREPARE_TOOLS = {"prepare_escalation", "prepare_ticket_update", "prepare_follow_up"}


@dataclass(frozen=True)
class Turn:
    """Everything a completed run produced, for callers that want it in one piece."""

    answer: str
    events: tuple[AgentEvent, ...]
    drafts: tuple[ActionDraft, ...]
    escalated: bool
    steps: int


class SupportAgent:
    """Runs one question to an answer, emitting events along the way."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        client: ModelClient,
        snapshot_at: datetime,
        max_steps: int = 12,
    ) -> None:
        self._registry = registry
        self._client = client
        self._snapshot_at = snapshot_at
        self._max_steps = max(1, max_steps)

    def run(
        self,
        caller: CallerContext,
        question: str,
        *,
        history: Sequence[Message] = (),
    ) -> Iterator[AgentEvent]:
        """Answer ``question`` as ``caller``, yielding progress as it happens."""
        messages: list[Message] = [
            Message(role="system", content=system_prompt(caller, self._snapshot_at)),
            *history,
            Message(role="user", content=question),
        ]
        tools = self._registry.schemas_for(caller.role)
        drafted: list[ActionDraft] = []

        for step in range(1, self._max_steps + 1):
            try:
                reply = self._client.reply(messages=messages, tools=tools)
            except ModelUnavailable as error:
                yield Failed(message=str(error))
                return

            if not reply.tool_calls:
                answer = reply.text.strip()
                yield TextDelta(text=answer, final=True)
                yield Completed(text=answer, steps=step, escalated=_has_escalation(drafted))
                return

            if reply.text.strip():  # commentary the model wrote before calling tools
                yield TextDelta(text=reply.text.strip())

            messages.append(
                Message(role="assistant", content=reply.text, tool_calls=reply.tool_calls)
            )

            for call in reply.tool_calls:
                events, result = self._run_call(call, caller, step)
                for event in events:
                    yield event
                    if isinstance(event, ActionDrafted):
                        drafted.append(event.draft)

                messages.append(
                    Message(
                        role="tool",
                        content=result.to_message_content(),
                        tool_call_id=call.call_id,
                    )
                )

        yield from self._escalate_at_ceiling(caller, drafted)

    # -- one tool call ----------------------------------------------------------

    def _run_call(
        self, call: ToolCall, caller: CallerContext, step: int
    ) -> tuple[list[AgentEvent], ToolCallResult]:
        tool = self._registry.get(call.name)
        mutating = bool(tool and tool.mutating)
        events: list[AgentEvent] = [
            ToolStarted(name=call.name, arguments=call.arguments, step=step, mutating=mutating)
        ]

        if call.parse_error:
            result = ToolCallResult(
                name=call.name,
                arguments={},
                ok=False,
                error=f"{call.parse_error}. Send the arguments as a JSON object.",
                mutating=mutating,
            )
        else:
            result = self._registry.dispatch(call.name, call.arguments, caller)

        events.append(
            ToolFinished(
                name=call.name,
                ok=result.ok,
                step=step,
                summary=_summarise(result),
                error=result.error,
                mutating=result.mutating or mutating,
                payload=result.payload if result.ok else None,
            )
        )

        if result.ok and call.name in PREPARE_TOOLS:
            draft = ActionDraft.from_dict(result.payload)
            events.append(ActionDrafted(draft=draft))
            if draft.kind is ActionKind.ESCALATION:
                events.append(
                    Escalated(
                        reason="the assistant judged that a person is needed",
                        detail=str(draft.details.get("reason", draft.summary)),
                        draft=draft,
                    )
                )

        return events, result

    # -- giving up --------------------------------------------------------------

    def _escalate_at_ceiling(
        self, caller: CallerContext, drafted: list[ActionDraft]
    ) -> Iterator[AgentEvent]:
        """Hand over rather than retry. Retrying would buy a worse version of this."""
        detail = (
            f"The assistant made {self._max_steps} tool calls without reaching an "
            "answer. A support agent should take this over."
        )
        result = self._registry.dispatch(
            "prepare_escalation",
            {"reason": detail, "severity": "P3"},
            caller,
        )

        draft: ActionDraft | None = None
        if result.ok:
            draft = ActionDraft.from_dict(result.payload)
            drafted.append(draft)
            yield ActionDrafted(draft=draft)

        yield Escalated(reason=CEILING_REASON, detail=detail, draft=draft)
        answer = (
            "I could not work this out from the documents available to me, so I have "
            "prepared a handover to a support agent. Confirm it and someone will pick "
            "this up."
        )
        yield TextDelta(text=answer, final=True)
        yield Completed(text=answer, steps=self._max_steps, escalated=True)


def collect(events: Iterator[AgentEvent]) -> Turn:
    """Drain a run into a :class:`Turn`. Convenient for scripts and tests."""
    gathered: list[AgentEvent] = []
    drafts: list[ActionDraft] = []
    answer = ""
    escalated = False
    steps = 0

    for event in events:
        gathered.append(event)
        if isinstance(event, ActionDrafted):
            drafts.append(event.draft)
        elif isinstance(event, Escalated):
            escalated = True
        elif isinstance(event, TextDelta) and event.final:
            answer = event.text
        elif isinstance(event, Completed):
            steps = event.steps
            escalated = escalated or event.escalated
        elif isinstance(event, Failed):
            answer = event.message

    return Turn(
        answer=answer,
        events=tuple(gathered),
        drafts=tuple(drafts),
        escalated=escalated,
        steps=steps,
    )


def _has_escalation(drafts: Sequence[ActionDraft]) -> bool:
    return any(draft.kind is ActionKind.ESCALATION for draft in drafts)


def _summarise(result: ToolCallResult) -> str:
    """A one-line description of what a tool call produced, for the transcript."""
    if not result.ok:
        return result.error or "failed"

    payload: Any = result.payload
    if isinstance(payload, dict):
        if "result_count" in payload:
            scope = payload.get("visible_scope")
            suffix = f" within {scope}" if scope else ""
            return f"{payload['result_count']} result(s){suffix}"
        if "status" in payload and payload.get("draft_id"):
            return f"draft prepared: {payload.get('summary', '')}"
        if "operation" in payload:
            return f"{payload['operation']} computed"
    return json.dumps(payload, default=str)[:120]

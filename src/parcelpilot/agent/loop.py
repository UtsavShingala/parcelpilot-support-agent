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

# How many times one tool may run in a single turn before further calls are refused.
#
# The repeat guard catches identical calls; this catches the other shape, where a
# model rephrases the same search over and over. Six distinct searches for one
# question is not thoroughness, it is not knowing when to stop -- and it cost three
# and a half minutes and twelve steps on an ops question that then escalated anyway.
# Three attempts at one tool is generous for a corpus of twenty-five passages.
MAX_CALLS_PER_TOOL = 3


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
        # Which calls have already run this turn, so an identical one can be answered
        # from what is already in the conversation instead of spending a step on it.
        completed: dict[str, int] = {}
        # How many times each tool has run, so one can be capped without capping all.
        used: dict[str, int] = {}
        # What was tried, in order, so a handover can say what ground was covered.
        attempted: list[str] = []

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
                attempted.append(_describe_attempt(call))
                events, result = self._run_call(call, caller, step, completed, used)
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

        yield from self._escalate_at_ceiling(caller, question, attempted)

    # -- one tool call ----------------------------------------------------------

    def _run_call(
        self,
        call: ToolCall,
        caller: CallerContext,
        step: int,
        completed: dict[str, int],
        used: dict[str, int],
    ) -> tuple[list[AgentEvent], ToolCallResult]:
        tool = self._registry.get(call.name)
        mutating = bool(tool and tool.mutating)
        events: list[AgentEvent] = [
            ToolStarted(name=call.name, arguments=call.arguments, step=step, mutating=mutating)
        ]

        spent = used.get(call.name, 0)
        if spent >= MAX_CALLS_PER_TOOL and call.name not in PREPARE_TOOLS:
            result = ToolCallResult(
                name=call.name,
                arguments=call.arguments,
                ok=True,
                payload=_budget_spent(call.name, spent),
                mutating=mutating,
            )
            events.append(
                ToolFinished(
                    name=call.name,
                    ok=True,
                    step=step,
                    summary=f"{call.name} already used {spent} times this turn",
                    mutating=mutating,
                )
            )
            return events, result

        signature = _signature(call)
        first_seen = completed.get(signature)
        if first_seen is not None:
            # Identical call, same turn. Running it again would return the same bytes
            # the model already has, and models that repeat a call tend to keep
            # repeating it until the step ceiling turns an answerable question into
            # an escalation. Answering from the transcript ends that cheaply.
            result = ToolCallResult(
                name=call.name,
                arguments=call.arguments,
                ok=True,
                payload=_already_answered(call.name, first_seen),
                mutating=mutating,
            )
            events.append(
                ToolFinished(
                    name=call.name,
                    ok=True,
                    step=step,
                    summary=f"already run at step {first_seen}; reusing that result",
                    mutating=mutating,
                    payload=None,
                )
            )
            return events, result

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

        if result.ok:
            completed[signature] = step
            used[call.name] = spent + 1

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
        self, caller: CallerContext, question: str, attempted: Sequence[str]
    ) -> Iterator[AgentEvent]:
        """Hand over rather than retry. Retrying would buy a worse version of this.

        The handover has to be usable by the person who receives it. A step count is
        a diagnostic about this system, not a description of anyone's problem -- the
        agent who picks this up needs the customer's question and what was already
        looked at, or they start from nothing and the escalation has cost the
        customer time rather than saving it.
        """
        detail = _handover_note(question, attempted, self._max_steps)
        result = self._registry.dispatch(
            "prepare_escalation",
            {"reason": detail, "severity": "P3"},
            caller,
        )

        draft: ActionDraft | None = None
        if result.ok:
            draft = ActionDraft.from_dict(result.payload)
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


def _signature(call: ToolCall) -> str:
    """Identity of a tool call: its name and its arguments, order-independent."""
    return json.dumps(
        {"name": call.name, "arguments": call.arguments}, sort_keys=True, default=str
    )


def _already_answered(name: str, step: int) -> dict[str, Any]:
    """What to hand back for a call that has already run this turn.

    Deliberately short. Repeating the payload would double the tokens and invite
    another repeat; a pointer to the earlier result costs almost nothing and tells
    the model plainly that it already has what it is asking for.
    """
    return {
        "repeat_of_step": step,
        "note": (
            f"You already called {name} with these exact arguments at step {step}, and "
            "its result is earlier in this conversation. Do not call it again. Use that "
            "result, or answer with what you have."
        ),
    }



def _describe_attempt(call: ToolCall) -> str:
    """One tool call, phrased for a person rather than a log."""
    subject = (
        call.arguments.get("query")
        or call.arguments.get("order_id")
        or call.arguments.get("ticket_id")
        or call.arguments.get("account_id")
        or ""
    )
    return f"{call.name}({subject})" if subject else call.name


def _handover_note(question: str, attempted: Sequence[str], ceiling: int) -> str:
    """What the support agent receiving this escalation needs to know."""
    asked = question.strip() or "an unstated question"
    unique: list[str] = []
    for attempt in attempted:
        if attempt not in unique:
            unique.append(attempt)

    covered = "; ".join(unique[:8]) or "nothing"
    return (
        f'The customer asked: "{asked}". The assistant could not reach a confident '
        f"answer within its {ceiling}-step limit and stopped rather than guess. "
        f"Already looked at: {covered}. Please pick this up from there."
    )

def _budget_spent(name: str, spent: int) -> dict[str, Any]:
    """Told plainly, once a tool has been leaned on enough for one question."""
    return {
        "calls_used": spent,
        "note": (
            f"You have already called {name} {spent} times for this question and its "
            "results are earlier in this conversation. Do not call it again. Answer "
            "from what you have, or if the documents genuinely do not cover this, "
            "prepare an escalation and say what is missing."
        ),
    }


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

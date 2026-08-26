"""What a run emits as it happens.

The loop is a generator of these rather than a function returning an answer. A
support answer is built from several tool calls, and both the person waiting and
the reviewer judging the system want to see that happen -- which tool ran, on what,
and what came back. Streaming the work is also what makes the interface honest: a
UI showing "searching agreements..." is reporting a fact, not animating a guess.

``Escalated`` and ``ActionDrafted`` overlap deliberately. A drafted escalation emits
both: it is an action awaiting confirmation *and* an outcome that needs a person.
A UI can badge one and banner the other without inspecting the draft's kind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from parcelpilot.agent.tools.actions import ActionDraft


@dataclass(frozen=True)
class ToolStarted:
    """A tool call is about to run."""

    name: str
    arguments: dict[str, Any]
    step: int
    mutating: bool = False
    type: str = field(default="tool_start", init=False)


@dataclass(frozen=True)
class ToolFinished:
    """A tool call returned."""

    name: str
    ok: bool
    step: int
    summary: str
    error: str | None = None
    mutating: bool = False
    type: str = field(default="tool_result", init=False)


@dataclass(frozen=True)
class TextDelta:
    """Assistant prose. Interim commentary between tool calls, or the final answer."""

    text: str
    final: bool = False
    type: str = field(default="text_delta", init=False)


@dataclass(frozen=True)
class ActionDrafted:
    """Something is prepared and waiting for the user to say yes."""

    draft: ActionDraft
    type: str = field(default="action_draft", init=False)


@dataclass(frozen=True)
class Escalated:
    """This turn needs a human.

    ``draft`` is present when an escalation was drafted for confirmation, and absent
    when the loop itself gave up -- there is nothing to confirm in that case, only
    something to report.
    """

    reason: str
    detail: str
    draft: ActionDraft | None = None
    type: str = field(default="escalation", init=False)


@dataclass(frozen=True)
class Completed:
    """The turn is over. ``text`` is the final answer, already emitted as deltas."""

    text: str
    steps: int
    escalated: bool = False
    type: str = field(default="completed", init=False)


@dataclass(frozen=True)
class Failed:
    """The turn could not run at all -- no credentials, provider outage.

    Distinct from an escalation: an escalation is the system working correctly and
    deciding a person is needed. This is the system not working.
    """

    message: str
    type: str = field(default="failed", init=False)


AgentEvent = (
    ToolStarted | ToolFinished | TextDelta | ActionDrafted | Escalated | Completed | Failed
)

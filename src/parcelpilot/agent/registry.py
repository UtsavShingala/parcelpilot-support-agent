"""The set of tools a caller may use, and the only way to invoke them.

Role filtering happens twice, and the two are not redundant.

Filtering :meth:`ToolRegistry.schemas_for` decides what the model is *told* exists.
A customer is never offered ``prepare_ticket_update``, so it never occurs to the
model to reach for it -- that is a usability measure, and it keeps the tool list
short, which measurably improves how reliably a model picks the right one.

Filtering inside :meth:`ToolRegistry.dispatch` decides what may actually *run*. This
is the enforcement. A model can emit a call for a tool it was never offered --
through a stale transcript, a prompt injection in a ticket description, or plain
confusion -- and the schema list does nothing to stop that. The dispatch check does.

Errors come back as results rather than exceptions. An unknown order or a missing
argument is information the agent should react to, by asking a better question or
escalating; raising would end the turn on something recoverable.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from parcelpilot.agent.tools.actions import build_action_tools
from parcelpilot.agent.tools.base import Tool, ToolError, ToolPermissionError
from parcelpilot.agent.tools.calculations import build_calculate
from parcelpilot.agent.tools.documents import build_search_documents
from parcelpilot.agent.tools.operational import build_operational_tools
from parcelpilot.auth.context import CallerContext, Role
from parcelpilot.data.queries import OperationalData
from parcelpilot.retrieval.store import DocumentStore


@dataclass(frozen=True)
class ToolCallResult:
    """What a tool call produced, successful or not."""

    name: str
    arguments: dict[str, Any]
    ok: bool
    payload: Any = None
    error: str | None = None
    mutating: bool = False

    def to_message_content(self) -> str:
        """The tool result as the model should see it."""
        if self.ok:
            return json.dumps(self.payload, default=str)
        return json.dumps({"error": self.error}, default=str)


@dataclass(frozen=True)
class ToolRegistry:
    """Every tool the system has, filtered per caller on the way in and out."""

    tools: tuple[Tool, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        names = [tool.name for tool in self.tools]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise ValueError(f"duplicate tool names: {', '.join(sorted(duplicates))}")

    def get(self, name: str) -> Tool | None:
        return next((tool for tool in self.tools if tool.name == name), None)

    def for_role(self, role: Role) -> list[Tool]:
        return [tool for tool in self.tools if tool.permits(role)]

    def schemas_for(self, role: Role) -> list[dict[str, Any]]:
        """Tool definitions to send to the model. A hint, not a control."""
        return [tool.schema() for tool in self.for_role(role)]

    def names_for(self, role: Role) -> list[str]:
        return [tool.name for tool in self.for_role(role)]

    def dispatch(
        self, name: str, arguments: dict[str, Any], caller: CallerContext
    ) -> ToolCallResult:
        """Run a tool call under this caller's authority. This is the enforcement point."""
        tool = self.get(name)
        if tool is None:
            return ToolCallResult(
                name=name,
                arguments=arguments,
                ok=False,
                error=(
                    f"there is no tool called {name!r}; available tools are "
                    f"{', '.join(self.names_for(caller.role))}"
                ),
            )

        if not tool.permits(caller.role):
            return ToolCallResult(
                name=name,
                arguments=arguments,
                ok=False,
                mutating=tool.mutating,
                error=(
                    f"a {caller.role.value} may not use {name}. Do not try to work around "
                    "this; if the user needs it done, prepare an escalation instead."
                ),
            )

        unexpected = set(arguments) - set(tool.parameters.get("properties", {}))
        if unexpected:
            return ToolCallResult(
                name=name,
                arguments=arguments,
                ok=False,
                mutating=tool.mutating,
                error=(
                    f"{name} does not accept {', '.join(sorted(unexpected))}; "
                    f"accepted arguments are "
                    f"{', '.join(sorted(tool.parameters.get('properties', {})))}"
                ),
            )

        try:
            payload = tool.handler(caller, **arguments)
        except (ToolError, ToolPermissionError) as error:
            return ToolCallResult(
                name=name,
                arguments=arguments,
                ok=False,
                mutating=tool.mutating,
                error=str(error),
            )
        except TypeError as error:  # a malformed call, not a crash worth ending the turn on
            return ToolCallResult(
                name=name,
                arguments=arguments,
                ok=False,
                mutating=tool.mutating,
                error=f"{name} was called incorrectly: {error}",
            )

        return ToolCallResult(
            name=name,
            arguments=arguments,
            ok=True,
            payload=payload,
            mutating=tool.mutating,
        )


def build_registry(store: DocumentStore, data: OperationalData) -> ToolRegistry:
    """Assemble every tool, bound to the resources it needs.

    Confirmation is not here. It is reachable only from the transport layer, once a
    person has said yes to a specific draft.
    """
    tools: Sequence[Tool] = [
        build_search_documents(store),
        *build_operational_tools(data),
        build_calculate(data),
        *build_action_tools(data),
    ]
    return ToolRegistry(tuple(tools))

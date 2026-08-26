"""What a tool is.

Every handler takes the :class:`~parcelpilot.auth.context.CallerContext` as its
first argument. Not as an option, not pulled from ambient state -- first argument,
always, so there is no way to write a tool that forgets to be scoped. The resources
a tool needs (the document store, the database) are bound when the registry is
built, which keeps that promise true without dragging plumbing through every
signature.

Tools also declare which roles may call them. The registry uses that twice: to
decide what a model is even told exists, and again to refuse a call. The second
check is the one that matters -- the first is only a hint to the model, and a hint
is not a control.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from parcelpilot.auth.context import Role

ToolHandler = Callable[..., Any]

ALL_ROLES = frozenset(Role)
INTERNAL_ONLY = frozenset({Role.SUPPORT_AGENT, Role.OPS_MANAGER})


class ToolError(Exception):
    """A tool could not do what was asked.

    Raised for conditions the model should see and react to -- an unknown order, a
    missing argument, a record belonging to someone else. These are reported back
    as tool results, not raised to the caller: the agent is expected to recover,
    ask a better question, or escalate.
    """


class ToolPermissionError(ToolError):
    """A caller attempted a tool their role may not use."""


@dataclass(frozen=True)
class Tool:
    """A callable the model may invoke, and the rules about who may invoke it."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    roles: frozenset[Role] = field(default=ALL_ROLES)
    mutating: bool = False

    def permits(self, role: Role) -> bool:
        return role in self.roles

    def schema(self) -> dict[str, Any]:
        """The tool as the OpenAI chat completions API expects it."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def object_schema(
    properties: dict[str, Any], *, required: list[str] | None = None
) -> dict[str, Any]:
    """A JSON Schema object, with additional properties refused.

    Refusing extras keeps a hallucinated argument from being silently ignored: the
    call fails visibly instead of running with something other than what the model
    thought it asked for.
    """
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def string_field(description: str, *, enum: list[str] | None = None) -> dict[str, Any]:
    field_schema: dict[str, Any] = {"type": "string", "description": description}
    if enum:
        field_schema["enum"] = enum
    return field_schema


def number_field(description: str) -> dict[str, Any]:
    return {"type": "number", "description": description}


def integer_field(description: str, *, minimum: int = 1, maximum: int = 50) -> dict[str, Any]:
    return {
        "type": "integer",
        "description": description,
        "minimum": minimum,
        "maximum": maximum,
    }


def boolean_field(description: str) -> dict[str, Any]:
    return {"type": "boolean", "description": description}

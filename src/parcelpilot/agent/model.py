"""The boundary between this system and whichever model serves it.

Everything above this module speaks in :class:`Message` and :class:`ModelReply`.
Nothing above it imports ``openai`` or knows what a ``tool_call_id`` is. Swapping
provider means writing one more class here that satisfies :class:`ModelClient` --
which is also what makes the loop testable without a key or a network, since a
scripted client is the same shape as a real one.

The provider was picked for where the credit is, not for capability, so paying a
little indirection to keep that decision reversible is worth it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class ToolCall:
    """A tool the model wants run."""

    call_id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    parse_error: str | None = None
    """Set when the model emitted arguments that were not valid JSON."""


@dataclass(frozen=True)
class Message:
    """One turn of conversation, in a form no provider owns."""

    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True)
class ModelReply:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


class ModelUnavailable(RuntimeError):
    """The model could not be reached, or was never configured."""


@runtime_checkable
class ModelClient(Protocol):
    """Anything that can answer a conversation, given tools it may call."""

    def reply(
        self, *, messages: Sequence[Message], tools: Sequence[dict[str, Any]]
    ) -> ModelReply: ...


class OpenAIModelClient:
    """:class:`ModelClient` backed by the OpenAI chat completions API."""

    def __init__(self, *, api_key: str, model: str, timeout: float = 60.0) -> None:
        if not api_key:
            raise ModelUnavailable(
                "no OPENAI_API_KEY is configured; the agent cannot run without a model"
            )
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, timeout=timeout)
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def reply(
        self, *, messages: Sequence[Message], tools: Sequence[dict[str, Any]]
    ) -> ModelReply:
        from openai import OpenAIError

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[_to_wire(message) for message in messages],  # type: ignore[arg-type]
                tools=list(tools) or None,  # type: ignore[arg-type]
            )
        except OpenAIError as error:
            raise ModelUnavailable(str(error)) from error

        choice = response.choices[0].message
        return ModelReply(
            text=choice.content or "",
            tool_calls=tuple(_from_wire(call) for call in (choice.tool_calls or [])),
        )


def available_models(*, api_key: str) -> list[str]:
    """Model ids this account can actually reach, newest first.

    Worth asking rather than assuming: the lineup moves, and a stale model id fails
    at the first real request rather than at configuration time.
    """
    if not api_key:
        raise ModelUnavailable("no OPENAI_API_KEY is configured")
    from openai import OpenAI, OpenAIError

    try:
        models = list(OpenAI(api_key=api_key).models.list())
    except OpenAIError as error:
        raise ModelUnavailable(str(error)) from error
    return [model.id for model in sorted(models, key=lambda m: -(m.created or 0))]


def _to_wire(message: Message) -> dict[str, Any]:
    """Translate a neutral message into the chat completions shape."""
    if message.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
        }

    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.call_id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
    return payload


def _from_wire(call: Any) -> ToolCall:
    """Translate a returned tool call, tolerating arguments that are not valid JSON."""
    raw = getattr(call.function, "arguments", "") or "{}"
    try:
        arguments = json.loads(raw)
    except json.JSONDecodeError as error:
        return ToolCall(
            call_id=call.id,
            name=call.function.name,
            parse_error=f"arguments were not valid JSON: {error}",
        )
    if not isinstance(arguments, dict):
        return ToolCall(
            call_id=call.id,
            name=call.function.name,
            parse_error="arguments must be a JSON object",
        )
    return ToolCall(call_id=call.id, name=call.function.name, arguments=arguments)

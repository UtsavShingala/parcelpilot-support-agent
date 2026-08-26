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

    provider_extra: dict[str, Any] | None = None
    """Opaque provider state that must be echoed back with this call.

    Gemini attaches a ``thought_signature`` to every function call and rejects the
    next request if it is not returned alongside the call it belongs to. Nothing
    above this module interprets it -- the loop carries the tool call around and the
    client puts it back on the wire -- so a provider requirement stays a provider
    detail instead of leaking into the agent.
    """


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


class CompatibleModelClient:
    """:class:`ModelClient` for any provider speaking the OpenAI chat-completions API.

    Named for the protocol rather than a vendor, because several providers implement
    it: OpenAI itself, Gemini through its compatibility endpoint, OpenRouter,
    Together. Pointing at a different one is a base URL and a model name.

    That is not a hypothetical. This project moved from OpenAI to Gemini mid-build
    when the credit ran out, and the change was two settings -- everything above
    this class, the tools and scoping and the loop, never noticed.
    """

    name = "live"
    """What an interface should call this mode. The scripted client answers too."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "",
        timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise ModelUnavailable(
                "no MODEL_API_KEY is configured; the agent cannot run without a model"
            )
        from openai import OpenAI

        self._client = OpenAI(
            api_key=api_key, timeout=timeout, **({"base_url": base_url} if base_url else {})
        )
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
            raise ModelUnavailable(_human_message(error)) from error

        choice = response.choices[0].message
        return ModelReply(
            text=choice.content or "",
            tool_calls=tuple(_from_wire(call) for call in (choice.tool_calls or [])),
        )


def _human_message(error: Exception) -> str:
    """Say what went wrong in a sentence someone can act on.

    Providers return their diagnostics as nested JSON meant for a log, and putting
    that in front of a visitor makes a working system look broken. The distinction
    that matters to a reader is short: is this temporary, is it spent, or is it
    misconfigured? The raw text is still appended for whoever is debugging.
    """
    raw = str(error)
    status = getattr(error, "status_code", None)
    lowered = raw.lower()

    if status == 429 or "resource_exhausted" in lowered or "quota" in lowered:
        headline = (
            "This demo has reached its request limit with the model provider. "
            "It resets on the provider's schedule; try again later."
        )
    elif status in {401, 403} or "api key" in lowered or "permission" in lowered:
        headline = "The model credentials are not valid, so the assistant cannot answer."
    elif status is not None and status >= 500:
        headline = "The model provider is having trouble. This is usually temporary."
    elif "timeout" in lowered or "timed out" in lowered or "connection" in lowered:
        headline = "The model could not be reached in time. This is usually temporary."
    else:
        return raw

    return f"{headline} (provider said: {_first_line(raw)})"


def _first_line(raw: str, limit: int = 160) -> str:
    """The first useful fragment of a provider error, without the JSON dump."""
    text = " ".join(raw.split())
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."


def available_models(*, api_key: str, base_url: str = "") -> list[str]:
    """Model ids this account can actually reach, newest first.

    Worth asking rather than assuming: the lineup moves, and a stale model id fails
    at the first real request rather than at configuration time.
    """
    if not api_key:
        raise ModelUnavailable("no MODEL_API_KEY is configured")
    from openai import OpenAI, OpenAIError

    try:
        client = OpenAI(api_key=api_key, **({"base_url": base_url} if base_url else {}))
        models = list(client.models.list())
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
        payload["tool_calls"] = [_call_to_wire(call) for call in message.tool_calls]
    return payload


def _call_to_wire(call: ToolCall) -> dict[str, Any]:
    wire: dict[str, Any] = {
        "id": call.call_id,
        "type": "function",
        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
    }
    if call.provider_extra:
        wire["extra_content"] = call.provider_extra
    return wire


def _from_wire(call: Any) -> ToolCall:
    """Translate a returned tool call, tolerating arguments that are not valid JSON."""
    extra = _provider_extra(call)
    raw = getattr(call.function, "arguments", "") or "{}"
    try:
        arguments = json.loads(raw)
    except json.JSONDecodeError as error:
        return ToolCall(
            call_id=call.id,
            name=call.function.name,
            parse_error=f"arguments were not valid JSON: {error}",
            provider_extra=extra,
        )
    if not isinstance(arguments, dict):
        return ToolCall(
            call_id=call.id,
            name=call.function.name,
            parse_error="arguments must be a JSON object",
            provider_extra=extra,
        )
    return ToolCall(
        call_id=call.id,
        name=call.function.name,
        arguments=arguments,
        provider_extra=extra,
    )


def _provider_extra(call: Any) -> dict[str, Any] | None:
    """Whatever the provider attached to this call, kept verbatim for the reply.

    Read defensively: it is an undeclared field on the SDK model, so it may arrive
    as an attribute or only in the extras dict, and most providers send none at all.
    """
    extra = getattr(call, "extra_content", None)
    if extra is None:
        extra = (getattr(call, "model_extra", None) or {}).get("extra_content")
    if extra is None:
        return None
    if hasattr(extra, "model_dump"):
        extra = extra.model_dump()
    return extra if isinstance(extra, dict) else None

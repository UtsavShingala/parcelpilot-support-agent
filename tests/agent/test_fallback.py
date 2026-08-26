"""When one model is unavailable, another should answer.

Over one afternoon three different Gemini models each returned 503 or exhausted a
daily quota while the others answered fine. Both conditions are per-model, so
retrying the same name harder is the one thing guaranteed not to help.
"""

from __future__ import annotations

from typing import Any

import pytest
from openai import APITimeoutError, AuthenticationError, InternalServerError, RateLimitError

from parcelpilot.agent.model import CompatibleModelClient, Message, ModelUnavailable

QUESTION = [Message(role="user", content="hello")]


def _error(kind: type, status: int) -> Exception:
    """Build an SDK error without a live response object."""
    error = kind.__new__(kind)
    Exception.__init__(error, f"synthetic {status}")
    error.status_code = status  # type: ignore[attr-defined]
    return error


class _Calls:
    """Stands in for the SDK, failing the first ``fail`` models it is asked for."""

    def __init__(self, failures: dict[str, Exception]) -> None:
        self.failures = failures
        self.asked: list[str] = []

    def create(self, *, model: str, **_: Any) -> Any:
        self.asked.append(model)
        if model in self.failures:
            raise self.failures[model]

        class _Function:
            name = "search_documents"
            arguments = "{}"

        class _Choice:
            class message:  # noqa: N801
                content = f"answered by {model}"
                tool_calls: list[Any] = []

        return type("Response", (), {"choices": [_Choice()]})()


def _client(failures: dict[str, Exception]) -> tuple[CompatibleModelClient, _Calls]:
    client = CompatibleModelClient(
        api_key="k", model="first", fallbacks=["second", "third"], base_url="http://x"
    )
    calls = _Calls(failures)
    client._client = type("Stub", (), {"chat": type("C", (), {"completions": calls})()})()
    return client, calls


def test_an_overloaded_model_falls_through_to_the_next() -> None:
    client, calls = _client({"first": _error(InternalServerError, 503)})
    reply = client.reply(messages=QUESTION, tools=[])

    assert reply.text == "answered by second"
    assert calls.asked == ["first", "second"]


def test_an_exhausted_quota_falls_through_too() -> None:
    """Free-tier quota is counted per model per day, so the next name is often fine."""
    client, calls = _client({"first": _error(RateLimitError, 429)})
    reply = client.reply(messages=QUESTION, tools=[])

    assert reply.text == "answered by second"


def test_it_keeps_going_until_one_answers() -> None:
    client, calls = _client(
        {"first": _error(InternalServerError, 503), "second": _error(RateLimitError, 429)}
    )
    reply = client.reply(messages=QUESTION, tools=[])

    assert reply.text == "answered by third"
    assert calls.asked == ["first", "second", "third"]


def test_a_timeout_tries_another_model() -> None:
    timeout = APITimeoutError.__new__(APITimeoutError)
    Exception.__init__(timeout, "Request timed out.")
    client, calls = _client({"first": timeout})

    assert client.reply(messages=QUESTION, tools=[]).text == "answered by second"


def test_a_bad_key_is_reported_rather_than_retried_around() -> None:
    """It would fail identically on every model; trying them all just wastes time."""
    client, calls = _client({"first": _error(AuthenticationError, 401)})

    with pytest.raises(ModelUnavailable, match="credentials"):
        client.reply(messages=QUESTION, tools=[])

    assert calls.asked == ["first"], "it tried other models for a key problem"


def test_when_every_model_fails_the_last_error_is_reported() -> None:
    failures = {name: _error(InternalServerError, 503) for name in ("first", "second", "third")}
    client, calls = _client(failures)

    with pytest.raises(ModelUnavailable, match="having trouble"):
        client.reply(messages=QUESTION, tools=[])

    assert calls.asked == ["first", "second", "third"]


def test_the_configured_model_is_tried_first() -> None:
    client, calls = _client({})
    client.reply(messages=QUESTION, tools=[])

    assert calls.asked == ["first"]
    assert client.model == "first"


def test_a_fallback_repeating_the_primary_is_not_tried_twice() -> None:
    client = CompatibleModelClient(
        api_key="k", model="first", fallbacks=["first", "second"], base_url="http://x"
    )
    assert client._models == ["first", "second"]

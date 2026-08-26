"""A second question must be able to refer to the first.

The plumbing existed and was never connected: the session carried a history list,
the loop accepted one, and nothing ever wrote to it. Every question arrived cold,
so "and what about that one?" had nothing to resolve against -- which is the first
thing anyone tries on a chatbot.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from parcelpilot.api.sessions import HISTORY_MESSAGES, SESSION_COOKIE, ChatSession
from parcelpilot.auth.context import CallerContext, Role
from parcelpilot.auth.personas import Persona

from .conftest import ask, sign_in

CANCELLATION = "Can I cancel ORD-1001 without a cancellation fee?"
SLA = "What is my first response target for a P1?"


def _blank_session() -> ChatSession:
    return ChatSession(
        session_id="s",
        persona=Persona(
            persona_id="acct-001",
            label="Northstar",
            description="Enterprise",
            context=CallerContext(role=Role.CUSTOMER, account_id="ACCT-001"),
        ),
    )


def _server_side(client: TestClient) -> ChatSession:
    """The session as the server holds it, which is where history actually lives."""
    store = client.app.state.runtime.sessions
    session = store.get(client.cookies.get(SESSION_COOKIE))
    assert session is not None, "not signed in"
    return session


# -- the window itself ----------------------------------------------------------


def test_an_exchange_is_remembered_as_question_then_answer() -> None:
    session = _blank_session()
    session.remember_exchange("what is the fee?", "INR 250 after thirty minutes.")

    assert [(m.role, m.content) for m in session.history] == [
        ("user", "what is the fee?"),
        ("assistant", "INR 250 after thirty minutes."),
    ]


def test_history_is_capped_and_drops_the_oldest_turns() -> None:
    """An unbounded history is an unbounded prompt, and an unbounded bill."""
    session = _blank_session()
    for index in range(10):
        session.remember_exchange(f"question {index}", f"answer {index}")

    assert len(session.history) == HISTORY_MESSAGES
    assert session.history[0].content == "question 7", "the window did not slide"
    assert session.history[-1].content == "answer 9"


def test_the_tool_transcript_is_not_replayed() -> None:
    """Only the question and the conclusion; past tool results would go stale."""
    session = _blank_session()
    session.remember_exchange("q", "a")

    assert all(message.role in {"user", "assistant"} for message in session.history)
    assert not any(message.tool_calls for message in session.history)


# -- over HTTP ------------------------------------------------------------------


def test_a_turn_leaves_its_question_and_answer_behind(client: TestClient) -> None:
    sign_in(client, "acct-001")
    assert _server_side(client).history == []

    ask(client, CANCELLATION)

    history = _server_side(client).history
    assert [message.role for message in history] == ["user", "assistant"]
    assert history[0].content == CANCELLATION
    assert history[1].content, "the answer was not kept"


def test_the_next_question_is_asked_with_the_previous_one_in_front_of_it(
    client: TestClient,
) -> None:
    """The whole point: the second turn sees the first."""
    sign_in(client, "acct-001")
    ask(client, CANCELLATION)

    seen: list[list[str]] = []
    agent = client.app.state.runtime.agent
    original = agent.run

    def capture(caller, question, *, history=()):  # noqa: ANN001, ANN202
        seen.append([message.content for message in history])
        return original(caller, question, history=history)

    agent.run = capture  # type: ignore[method-assign]
    try:
        ask(client, "And what about that one?")
    finally:
        agent.run = original  # type: ignore[method-assign]

    assert seen, "the agent was never called"
    assert CANCELLATION in seen[0], "the follow-up did not carry the first question"


def test_history_accumulates_across_turns(client: TestClient) -> None:
    sign_in(client, "acct-001")
    ask(client, CANCELLATION)
    ask(client, SLA)

    contents = [message.content for message in _server_side(client).history]
    assert CANCELLATION in contents
    assert SLA in contents


def test_switching_persona_does_not_carry_a_conversation_over(client: TestClient) -> None:
    """A new sign-in is a new caller; inheriting the last one's chat would leak it."""
    sign_in(client, "acct-001")
    ask(client, CANCELLATION)

    sign_in(client, "acct-002")
    assert _server_side(client).history == []

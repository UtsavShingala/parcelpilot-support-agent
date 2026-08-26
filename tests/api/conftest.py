"""A running service, in scripted mode, for the HTTP tests.

Scripted mode is what makes these tests meaningful without a key: the tools, the
scoping and the confirmation split are all real, and only the wording of the final
answer is assembled. A test that mocked the agent would prove the routes call
something, not that a customer cannot reach another account.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from parcelpilot.api.main import create_app
from parcelpilot.config import Settings


@pytest.fixture(scope="module")
def client(corpus_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    """The service with its own action ledger.

    The ledger is the one piece of real, durable state here. Sharing the deployed
    one would make these tests order-dependent and would file demo escalations into
    a file that is meant to record things the system actually did.
    """
    ledger = tmp_path_factory.mktemp("ledger") / "actions.db"
    with TestClient(create_app(Settings(scripted=True, actions_db=ledger))) as running:
        yield running


@pytest.fixture
def fresh(corpus_dir: Path, tmp_path: Path) -> Iterator[TestClient]:
    """An isolated service, for tests that exhaust a session's allowance."""
    settings = Settings(
        scripted=True, max_messages_per_session=2, actions_db=tmp_path / "actions.db"
    )
    with TestClient(create_app(settings)) as running:
        yield running


def sign_in(client: TestClient, persona_id: str) -> dict[str, Any]:
    response = client.post("/api/session", json={"persona_id": persona_id})
    assert response.status_code == 200, response.text
    return response.json()


def ask(client: TestClient, question: str) -> list[dict[str, Any]]:
    """Send a question and collect the parsed SSE frames."""
    with client.stream("POST", "/api/chat", json={"question": question}) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())
    return parse_sse(body)


def parse_sse(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for frame in body.split("\n\n"):
        for line in frame.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


def tools_in(events: list[dict[str, Any]]) -> list[str]:
    return [event["name"] for event in events if event["type"] == "tool_start"]


def final_answer(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        if event["type"] == "completed":
            return str(event["text"])
    raise AssertionError(f"no completed event in {[e['type'] for e in events]}")

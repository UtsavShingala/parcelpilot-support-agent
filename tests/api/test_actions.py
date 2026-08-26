"""Only a person can execute an action, and only one the server already holds."""

from __future__ import annotations

from fastapi.testclient import TestClient

from parcelpilot.agent.registry import build_registry
from parcelpilot.data.queries import OperationalData
from parcelpilot.retrieval.store import DocumentStore

from .conftest import ask, sign_in

ESCALATE = "Please escalate this to a human, I need someone to look at it"


def _draft_id(client: TestClient, question: str = ESCALATE) -> str:
    for event in ask(client, question):
        if event["type"] == "action_draft":
            return str(event["draft"]["draft_id"])
    raise AssertionError("no draft was prepared")


def test_confirmation_is_not_a_tool_the_model_can_reach() -> None:
    """The structural half of the guarantee: there is no tool to call."""
    data = OperationalData.open()
    try:
        registry = build_registry(DocumentStore.from_settings(), data)
        names = {tool.name for tool in registry.tools}
    finally:
        data.close()

    assert not any("confirm" in name for name in names)
    assert {"prepare_escalation"} <= names, "preparing must still be available"


def test_preparing_writes_nothing_until_confirmed(client: TestClient) -> None:
    sign_in(client, "acct-001")
    events = ask(client, ESCALATE)

    draft = next(e for e in events if e["type"] == "action_draft")["draft"]
    assert draft["status"] == "awaiting confirmation"
    assert client.get("/api/actions").json()["actions"] == []


def test_a_drafted_escalation_is_also_reported_as_an_escalation(client: TestClient) -> None:
    sign_in(client, "acct-001")
    kinds = [event["type"] for event in ask(client, ESCALATE)]
    assert "escalation" in kinds


def test_confirming_records_the_action(client: TestClient) -> None:
    sign_in(client, "acct-001")
    draft_id = _draft_id(client)

    response = client.post("/api/actions/confirm", json={"draft_id": draft_id})
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "confirmed"
    assert body["action"]["kind"] == "escalation"
    assert body["action"]["performed_by"].startswith("Northstar")


def test_confirming_twice_does_not_file_it_twice(client: TestClient) -> None:
    sign_in(client, "acct-001")
    draft_id = _draft_id(client)

    first = client.post("/api/actions/confirm", json={"draft_id": draft_id}).json()
    second = client.post("/api/actions/confirm", json={"draft_id": draft_id}).json()

    assert second["status"] == "already recorded"
    assert second["action"]["action_id"] == first["action"]["action_id"]


def test_a_draft_the_browser_invented_is_refused(client: TestClient) -> None:
    """The request names a draft; it never carries one."""
    sign_in(client, "acct-001")
    response = client.post("/api/actions/confirm", json={"draft_id": "made-up-id"})

    assert response.status_code == 404
    assert "awaiting confirmation" in response.json()["detail"]


def test_one_visitors_draft_cannot_be_confirmed_by_another(client: TestClient) -> None:
    sign_in(client, "acct-001")
    draft_id = _draft_id(client)

    sign_in(client, "acct-002")  # a different session entirely
    response = client.post("/api/actions/confirm", json={"draft_id": draft_id})
    assert response.status_code == 404


def test_confirming_without_a_session_is_refused(client: TestClient) -> None:
    client.cookies.clear()
    response = client.post("/api/actions/confirm", json={"draft_id": "anything"})
    assert response.status_code == 401


def test_a_customer_sees_only_their_own_confirmed_actions(client: TestClient) -> None:
    sign_in(client, "acct-001")
    client.post("/api/actions/confirm", json={"draft_id": _draft_id(client)})

    sign_in(client, "acct-002")
    visible = client.get("/api/actions").json()
    assert all(action["account_id"] != "ACCT-001" for action in visible["actions"])
    assert visible["internal"] is False


def test_internal_staff_see_across_accounts(client: TestClient) -> None:
    sign_in(client, "acct-001")
    client.post("/api/actions/confirm", json={"draft_id": _draft_id(client)})

    sign_in(client, "ops-manager")
    visible = client.get("/api/actions").json()
    assert visible["internal"] is True
    assert any(action["account_id"] == "ACCT-001" for action in visible["actions"])

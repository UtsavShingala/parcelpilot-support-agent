"""The browser learns who it is signed in as, and nothing about what that permits."""

from __future__ import annotations

from fastapi.testclient import TestClient

from parcelpilot.api.sessions import SESSION_COOKIE

from .conftest import sign_in


def test_the_roster_is_public_but_says_nothing_about_authority(client: TestClient) -> None:
    payload = client.get("/api/personas").json()

    assert payload["personas"], "the picker would be empty"
    for persona in payload["personas"]:
        assert set(persona) == {"persona_id", "label", "description"}
        assert "ACCT-" not in persona["description"], "an account id reached the browser"


def test_the_roster_states_the_snapshot_and_the_mode(client: TestClient) -> None:
    payload = client.get("/api/personas").json()
    assert payload["snapshot_at"].startswith("2026-")
    assert payload["mode"] == "scripted"
    assert "no model is called" in payload["mode_description"]


def test_signing_in_sets_an_opaque_cookie(client: TestClient) -> None:
    response = client.post("/api/session", json={"persona_id": "acct-001"})

    assert response.status_code == 200
    token = response.cookies.get(SESSION_COOKIE) or client.cookies.get(SESSION_COOKIE)
    assert token and len(token) > 20, "the session id should be unguessable"
    assert "acct-001" not in token, "the session id encodes the persona"


def test_the_session_payload_carries_no_role_or_account(client: TestClient) -> None:
    payload = sign_in(client, "acct-001")

    assert set(payload["persona"]) == {"persona_id", "label", "description"}
    assert "role" not in payload["persona"]
    assert "account_id" not in payload["persona"]
    assert payload["messages_allowed"] > 0


def test_an_unknown_persona_is_refused_with_the_options(client: TestClient) -> None:
    response = client.post("/api/session", json={"persona_id": "acct-999"})

    assert response.status_code == 404
    assert "acct-001" in response.json()["detail"]


def test_whoami_needs_a_session(client: TestClient) -> None:
    client.cookies.clear()
    assert client.get("/api/session").status_code == 401


def test_whoami_restores_a_reloaded_page(client: TestClient) -> None:
    sign_in(client, "acct-002")
    payload = client.get("/api/session").json()
    assert payload["persona"]["persona_id"] == "acct-002"


def test_signing_out_invalidates_the_session(client: TestClient) -> None:
    sign_in(client, "acct-001")
    assert client.delete("/api/session").status_code == 200
    assert client.get("/api/session").status_code == 401


def test_a_forged_session_id_is_simply_unknown(client: TestClient) -> None:
    """There is no claim to tamper with: an id the server does not hold is nobody."""
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, "not-a-real-session-id")
    assert client.get("/api/session").status_code == 401


def test_health_reports_the_mode_without_a_session(client: TestClient) -> None:
    client.cookies.clear()
    payload = client.get("/api/health").json()
    assert payload["status"] == "ok"
    assert payload["mode"] == "scripted"

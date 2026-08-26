"""The operations view is staff-only, and says what it is looking at."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import ask, sign_in

ESCALATE = "Please escalate this to a human"


def test_a_customer_is_refused(client: TestClient) -> None:
    sign_in(client, "acct-001")
    response = client.get("/api/insights")

    assert response.status_code == 403
    assert "staff" in response.json()["detail"]


def test_signing_out_is_not_a_way_in(client: TestClient) -> None:
    client.cookies.clear()
    assert client.get("/api/insights").status_code == 401


def test_staff_see_the_signals(client: TestClient) -> None:
    sign_in(client, "ops-manager")
    payload = client.get("/api/insights").json()

    assert payload["signals"], "the ops view found nothing to report"
    assert payload["scope"] == "all accounts"
    assert payload["snapshot_at"].startswith("2026-")


def test_the_most_urgent_signal_leads(client: TestClient) -> None:
    sign_in(client, "ops-manager")
    signals = client.get("/api/insights").json()["signals"]

    assert signals[0]["severity"] == "P1"
    assert signals[0]["tickets"], "a signal with no evidence is not actionable"


def test_breaches_carry_the_clause_they_missed(client: TestClient) -> None:
    sign_in(client, "ops-manager")
    signals = client.get("/api/insights").json()["signals"]

    breaches = [signal for signal in signals if signal["kind"] == "sla_breached"]
    assert breaches
    for breach in breaches:
        assert breach["target"], "no target stated"
        assert breach["citations"], "no source for the target"
        assert breach["elapsed_minutes"] is not None


def test_counts_summarise_what_was_found(client: TestClient) -> None:
    sign_in(client, "ops-manager")
    payload = client.get("/api/insights").json()

    assert sum(payload["counts"].values()) == len(payload["signals"])
    assert payload["counts"].get("P1", 0) >= 1


def test_confirmed_escalations_appear_in_the_queue(client: TestClient) -> None:
    """The gap this closes: escalations were real and had nowhere to be seen."""
    sign_in(client, "acct-002")
    draft_id = next(
        event["draft"]["draft_id"]
        for event in ask(client, ESCALATE)
        if event["type"] == "action_draft"
    )
    client.post("/api/actions/confirm", json={"draft_id": draft_id})

    sign_in(client, "ops-manager")
    escalations = client.get("/api/insights").json()["escalations"]

    assert any(record["draft_id"] == draft_id for record in escalations)
    assert any(record["account_id"] == "ACCT-002" for record in escalations)


def test_a_support_agent_reaches_it_too(client: TestClient) -> None:
    """Both internal roles, not only the manager: agents triage, so agents need it."""
    sign_in(client, "agent-rohit")
    payload = client.get("/api/insights").json()

    assert payload["signals"]
    assert payload["scope"] == "all accounts"

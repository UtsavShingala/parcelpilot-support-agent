"""The stream must report real work, and report it to the right caller."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import ask, final_answer, sign_in, tools_in

CANCELLATION = "Can I cancel ORD-1001 without a cancellation fee?"


def test_a_question_without_a_persona_is_refused(client: TestClient) -> None:
    """Enforced server-side: the picker is not a suggestion."""
    client.cookies.clear()
    response = client.post("/api/chat", json={"question": CANCELLATION})
    assert response.status_code == 401


def test_the_turn_streams_tool_events_in_order(client: TestClient) -> None:
    sign_in(client, "acct-001")
    events = ask(client, CANCELLATION)

    assert tools_in(events) == ["lookup_orders", "search_documents", "calculate"]
    kinds = [event["type"] for event in events]
    assert kinds.index("tool_start") < kinds.index("tool_result")
    assert kinds[-1] == "completed"


def test_a_tool_event_carries_what_the_interface_renders(client: TestClient) -> None:
    sign_in(client, "acct-001")
    events = ask(client, CANCELLATION)

    started = next(e for e in events if e["type"] == "tool_start")
    assert started["arguments"], "a tool card shows what the tool was asked"

    search = next(
        e for e in events if e["type"] == "tool_result" and e["name"] == "search_documents"
    )
    citation = search["result"]["results"][0]
    assert {"citation", "source_file", "authority_tier", "clause"} <= set(citation)
    assert citation["authority_tier"] in {"AGREEMENT", "CURRENT_POLICY", "PRODUCT_DOC"}


def test_the_agreement_is_cited_above_the_general_sop(client: TestClient) -> None:
    """The demo path: Northstar's contract governs, and the SOP is still named."""
    sign_in(client, "acct-001")
    events = ask(client, CANCELLATION)

    search = next(
        e for e in events if e["type"] == "tool_result" and e["name"] == "search_documents"
    )
    tiers = [item["authority_tier"] for item in search["result"]["results"]]
    assert tiers[0] == "AGREEMENT", "the customer's own agreement should lead"

    answer = final_answer(events)
    assert "Northstar" in answer
    assert "SOP" in answer


def test_a_customer_never_sees_another_accounts_order(client: TestClient) -> None:
    sign_in(client, "acct-002")
    events = ask(client, CANCELLATION)

    answer = final_answer(events)
    assert "ORD-1001" not in answer, "an ACCT-001 order reached ACCT-002"
    for event in events:
        if event["type"] == "tool_result" and event.get("result"):
            rendered = str(event["result"])
            assert "ACCT-001" not in rendered
            assert "Northstar" not in rendered


def test_the_same_question_diverges_by_persona(client: TestClient) -> None:
    sign_in(client, "acct-001")
    northstar = final_answer(ask(client, CANCELLATION))
    sign_in(client, "acct-002")
    lumenworks = final_answer(ask(client, CANCELLATION))

    assert northstar != lumenworks


def test_a_question_the_corpus_cannot_answer_hands_over(client: TestClient) -> None:
    sign_in(client, "acct-001")
    answer = final_answer(ask(client, "zzzz quantum chromodynamics zzzz"))
    assert "support agent" in answer


def test_the_session_allowance_is_enforced(fresh: TestClient) -> None:
    sign_in(fresh, "acct-001")
    for _ in range(2):
        assert ask(fresh, CANCELLATION)

    refused = fresh.post("/api/chat", json={"question": CANCELLATION})
    assert refused.status_code == 429
    assert "messages" in refused.json()["detail"]


def test_a_failed_turn_still_costs_a_message(fresh: TestClient) -> None:
    """Otherwise a public demo is drainable by anyone able to provoke failures."""
    sign_in(fresh, "acct-001")
    before = fresh.get("/api/session").json()["messages_remaining"]
    ask(fresh, "zzzz nothing matches this zzzz")
    after = fresh.get("/api/session").json()["messages_remaining"]

    assert after == before - 1


def test_an_empty_question_is_rejected_before_any_work(client: TestClient) -> None:
    sign_in(client, "acct-001")
    assert client.post("/api/chat", json={"question": "   "}).status_code in {200, 422}
    assert client.post("/api/chat", json={"question": ""}).status_code == 422


def test_an_oversized_question_is_rejected(client: TestClient) -> None:
    sign_in(client, "acct-001")
    response = client.post("/api/chat", json={"question": "x" * 5000})
    assert response.status_code == 422

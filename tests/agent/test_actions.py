"""Preparing must change nothing; confirming must be the only thing that changes anything."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from parcelpilot.agent.tools.actions import (
    ActionKind,
    ActionLedger,
    build_action_tools,
    prepare_escalation,
    prepare_follow_up,
    prepare_ticket_update,
)
from parcelpilot.agent.tools.base import ToolError, ToolPermissionError
from parcelpilot.auth.context import CallerContext, Role
from parcelpilot.data.queries import OperationalData

SUPPORT = CallerContext(role=Role.SUPPORT_AGENT, display_name="Maya")
OPS = CallerContext(role=Role.OPS_MANAGER, display_name="Ops")
NORTHSTAR = CallerContext(role=Role.CUSTOMER, account_id="ACCT-001", display_name="Northstar")
LUMEN = CallerContext(role=Role.CUSTOMER, account_id="ACCT-002", display_name="LumenWorks")


@pytest.fixture(scope="module")
def data(corpus_dir: Path) -> Iterator[OperationalData]:
    operational = OperationalData.open()
    yield operational
    operational.close()


@pytest.fixture
def ledger(tmp_path: Path, data: OperationalData) -> Iterator[ActionLedger]:
    handle = ActionLedger(tmp_path / "actions.db", effective_at=data.snapshot_at)
    yield handle
    handle.close()


def _a_ticket(data: OperationalData, account_id: str) -> str:
    rows = data.tickets(SUPPORT, account_id=account_id, limit=1)
    if not rows:
        pytest.skip(f"no ticket on {account_id}")
    return str(rows[0]["ticket_id"])


# -- preparing writes nothing ---------------------------------------------------


def test_preparing_leaves_the_ledger_empty(data: OperationalData, ledger: ActionLedger) -> None:
    prepare_escalation(data, SUPPORT, reason="needs a human", severity="P2")
    prepare_follow_up(data, SUPPORT, subject="chase the carrier", owner="Maya")
    prepare_ticket_update(
        data, SUPPORT, ticket_id=_a_ticket(data, "ACCT-001"), status="pending"
    )

    assert ledger.records(OPS) == []


def test_the_prepare_tool_says_plainly_that_nothing_happened(data: OperationalData) -> None:
    tool = next(t for t in build_action_tools(data) if t.name == "prepare_escalation")
    result = tool.handler(SUPPORT, reason="carrier dispute needs a decision", severity="P2")

    assert result["status"] == "awaiting confirmation"
    assert "Nothing has been done yet" in result["instruction"]
    assert "completed" in result["instruction"]


def test_preparing_the_same_action_twice_yields_one_draft(data: OperationalData) -> None:
    first = prepare_escalation(data, SUPPORT, reason="same reason", severity="P1")
    second = prepare_escalation(data, SUPPORT, reason="same reason", severity="P1")
    assert first.draft_id == second.draft_id


def test_different_actions_get_different_drafts(data: OperationalData) -> None:
    first = prepare_escalation(data, SUPPORT, reason="one thing", severity="P1")
    second = prepare_escalation(data, SUPPORT, reason="another thing", severity="P1")
    assert first.draft_id != second.draft_id


# -- confirming is the only writer ----------------------------------------------


def test_confirming_records_the_action(data: OperationalData, ledger: ActionLedger) -> None:
    draft = prepare_escalation(data, SUPPORT, reason="needs a human", severity="P2")
    record = ledger.confirm(draft, SUPPORT)

    assert record.kind is ActionKind.ESCALATION
    assert record.details["severity"] == "P2"
    assert record.performed_by.startswith("Maya")
    assert [row.draft_id for row in ledger.records(OPS)] == [draft.draft_id]


def test_confirming_twice_does_not_file_it_twice(
    data: OperationalData, ledger: ActionLedger
) -> None:
    draft = prepare_escalation(data, SUPPORT, reason="double click", severity="P3")
    first = ledger.confirm(draft, SUPPORT)
    second = ledger.confirm(draft, SUPPORT)

    assert first.action_id == second.action_id
    assert len(ledger.records(OPS)) == 1


def test_the_ledger_keeps_both_timelines(
    data: OperationalData, ledger: ActionLedger
) -> None:
    """effective_at keeps the dataset timeline; recorded_at is the audit fact."""
    draft = prepare_escalation(data, SUPPORT, reason="timeline check", severity="P3")
    record = ledger.confirm(draft, SUPPORT)

    assert record.effective_at == data.snapshot_at.isoformat()
    assert record.recorded_at != record.effective_at


# -- authorisation is re-checked at confirmation --------------------------------


def test_a_customer_may_escalate_their_own_issue(
    data: OperationalData, ledger: ActionLedger
) -> None:
    draft = prepare_escalation(data, NORTHSTAR, reason="my pickup never happened")
    record = ledger.confirm(draft, NORTHSTAR)
    assert record.account_id == "ACCT-001"


def test_a_customer_may_not_confirm_a_ticket_update(
    data: OperationalData, ledger: ActionLedger
) -> None:
    """Even holding a valid draft, the role check runs again at confirmation."""
    draft = prepare_ticket_update(
        data, SUPPORT, ticket_id=_a_ticket(data, "ACCT-001"), status="closed"
    )
    with pytest.raises(ToolPermissionError, match="may not perform"):
        ledger.confirm(draft, NORTHSTAR)

    assert ledger.records(OPS) == []


def test_a_customer_may_not_confirm_an_action_on_another_account(
    data: OperationalData, ledger: ActionLedger
) -> None:
    draft = prepare_escalation(data, LUMEN, reason="my problem")
    assert draft.account_id == "ACCT-002"

    with pytest.raises(ToolPermissionError, match="ACCT-002"):
        ledger.confirm(draft, NORTHSTAR)


def test_a_tampered_draft_is_refused_at_confirmation(
    data: OperationalData, ledger: ActionLedger
) -> None:
    """A draft is data; by the time it comes back it has been outside the process."""
    import dataclasses

    honest = prepare_escalation(data, NORTHSTAR, reason="my problem")
    tampered = dataclasses.replace(honest, account_id="ACCT-002")

    with pytest.raises(ToolPermissionError):
        ledger.confirm(tampered, NORTHSTAR)


# -- preparing respects scope ---------------------------------------------------


def test_a_customer_cannot_prepare_against_another_accounts_ticket(
    data: OperationalData,
) -> None:
    other = _a_ticket(data, "ACCT-002")
    with pytest.raises(ToolError, match="visible"):
        prepare_escalation(data, NORTHSTAR, reason="curious", ticket_id=other)


def test_a_ticket_update_needs_something_to_change(data: OperationalData) -> None:
    with pytest.raises(ToolError, match="status, a note"):
        prepare_ticket_update(data, SUPPORT, ticket_id=_a_ticket(data, "ACCT-001"))


def test_an_escalation_needs_an_actionable_reason(data: OperationalData) -> None:
    with pytest.raises(ToolError, match="reason"):
        prepare_escalation(data, SUPPORT, reason="   ")


def test_an_unknown_severity_is_refused(data: OperationalData) -> None:
    with pytest.raises(ToolError, match="severity"):
        prepare_escalation(data, SUPPORT, reason="valid", severity="P9")


# -- who sees what --------------------------------------------------------------


def test_customers_see_only_their_own_confirmed_actions(
    data: OperationalData, ledger: ActionLedger
) -> None:
    ledger.confirm(prepare_escalation(data, NORTHSTAR, reason="mine"), NORTHSTAR)
    ledger.confirm(prepare_escalation(data, LUMEN, reason="theirs"), LUMEN)

    assert [row.details["reason"] for row in ledger.records(NORTHSTAR)] == ["mine"]
    assert [row.details["reason"] for row in ledger.records(LUMEN)] == ["theirs"]
    assert len(ledger.records(OPS)) == 2


def test_the_ledger_survives_a_reopen(data: OperationalData, tmp_path: Path) -> None:
    path = tmp_path / "actions.db"
    first = ActionLedger(path, effective_at=data.snapshot_at)
    first.confirm(prepare_escalation(data, SUPPORT, reason="persisted"), SUPPORT)
    first.close()

    second = ActionLedger(path, effective_at=data.snapshot_at)
    assert [row.details["reason"] for row in second.records(OPS)] == ["persisted"]
    second.close()

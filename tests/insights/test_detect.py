"""Proactive detection must be accurate, grounded, and scoped like everything else.

A dashboard that cries wolf gets ignored after the second look, so these lean hard
on precision: a cluster that is not really a cluster is worse than a missed one,
because it reads as "two customers are affected".
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from parcelpilot.auth.context import CallerContext, Role
from parcelpilot.data.queries import OperationalData
from parcelpilot.ingest.build_index import load_chunks
from parcelpilot.ingest.documents import Chunk
from parcelpilot.insights.detect import Signal, detect
from parcelpilot.insights.severity import classify
from parcelpilot.insights.targets import parse_duration, targets_for

OPS = CallerContext(role=Role.OPS_MANAGER, display_name="Ops")
NORTHSTAR = CallerContext(role=Role.CUSTOMER, account_id="ACCT-001", display_name="Northstar")


@pytest.fixture(scope="module")
def chunks(corpus_dir: Path) -> list[Chunk]:
    return load_chunks()


@pytest.fixture(scope="module")
def data(corpus_dir: Path) -> Iterator[OperationalData]:
    operational = OperationalData.open()
    yield operational
    operational.close()


@pytest.fixture(scope="module")
def signals(data: OperationalData, chunks: list[Chunk]) -> list[Signal]:
    return detect(data, OPS, chunks)


def _of_kind(signals: list[Signal], kind: str) -> list[Signal]:
    return [signal for signal in signals if signal.kind == kind]


# -- severity, from the policy's own words --------------------------------------


def test_a_total_outage_is_a_p1() -> None:
    found = classify("All shipment creation is failing", "Every user gets HTTP 500.")
    assert found.level == "P1"
    assert "complete production outage" in found.because


def test_a_credential_leak_is_a_p1() -> None:
    found = classify(
        "Possible API key exposure", "An employee posted a screenshot with a production key."
    )
    assert found.level == "P1"
    assert "credential exposure" in found.because


def test_a_degraded_feature_is_a_p2() -> None:
    """Guards a word-boundary bug: "fails" was falling through to the default."""
    assert classify("Bulk upload fails for 4,200-row CSV").level == "P2"
    assert classify("Bulk upload failed").level == "P2"
    assert classify("Bulk upload failure").level == "P2"


def test_a_how_to_question_is_a_p3() -> None:
    assert classify("How do we change the billing contact?").level == "P3"


def test_an_unrecognised_ticket_defaults_and_says_so() -> None:
    found = classify("Something unusual happened")
    assert found.level == "P3"
    assert not found.confident


# -- targets, read from the corpus ----------------------------------------------


def test_an_agreement_overrides_the_plan_default(chunks: list[Chunk]) -> None:
    """Northstar is Enterprise, whose default P1 is 30 minutes; their contract says 15."""
    targets = targets_for(chunks, account_id="ACCT-001", plan="Enterprise")
    assert targets["P1"].minutes == 15
    assert "Northstar" in targets["P1"].source


def test_an_account_without_an_agreement_uses_the_plan_default(chunks: list[Chunk]) -> None:
    targets = targets_for(chunks, account_id="ACCT-004", plan="Enterprise")
    assert targets["P1"].minutes == 30
    assert "Support Policy v3" in targets["P1"].source


def test_targets_never_come_from_the_superseded_policy(chunks: list[Chunk]) -> None:
    """v2 lists a one-hour Enterprise P1 and must never be the source of a target."""
    targets = targets_for(chunks, account_id="ACCT-004", plan="Enterprise")
    assert all("v2" not in target.source for target in targets.values())
    assert targets["P1"].minutes != 60


def test_business_time_is_not_converted_into_a_deadline() -> None:
    """Assuming an eight-hour day would invent a calendar the corpus never states."""
    assert parse_duration("30 minutes, 24x7") == 30
    assert parse_duration("2 hours") == 120
    assert parse_duration("4 business hours") is None
    assert parse_duration("2 business days") is None


# -- response targets against the snapshot --------------------------------------


def test_breaches_are_measured_against_the_snapshot_not_today(signals: list[Signal]) -> None:
    breached = {signal.tickets[0] for signal in _of_kind(signals, "sla_breached")}
    assert breached == {"TKT-501", "TKT-505"}


def test_a_breach_states_the_target_it_missed_and_cites_it(signals: list[Signal]) -> None:
    breach = next(s for s in _of_kind(signals, "sla_breached") if s.tickets == ("TKT-501",))

    assert breach.severity == "P1"
    assert breach.elapsed_minutes == 30
    assert breach.target == "15 minutes, 24x7"
    assert "Northstar" in breach.citations[0], "the governing clause was not cited"


def test_a_business_hours_target_is_handed_to_a_person(signals: list[Signal]) -> None:
    manual = _of_kind(signals, "needs_manual_check")
    assert any(signal.tickets == ("TKT-502",) for signal in manual)
    assert all("calendar" in signal.detail for signal in manual)


# -- one fault, several tickets -------------------------------------------------


def test_tickets_are_clustered_onto_the_known_issue_they_match(
    signals: list[Signal],
) -> None:
    clusters = _of_kind(signals, "issue_cluster") + _of_kind(signals, "multi_account_issue")
    bulk = next(signal for signal in clusters if "KI-208" in signal.title)

    assert set(bulk.tickets) == {"TKT-502", "TKT-451"}
    assert "Product Operations Guide" in bulk.citations[0]


def test_an_unrelated_ticket_is_not_swept_into_a_cluster(signals: list[Signal]) -> None:
    """A total outage was being filed under "Bulk Upload failures" on two shared words."""
    clusters = _of_kind(signals, "issue_cluster") + _of_kind(signals, "multi_account_issue")
    for cluster in clusters:
        if "KI-208" in cluster.title:
            assert "TKT-501" not in cluster.tickets
        if "KI-211" in cluster.title:
            assert "TKT-450" not in cluster.tickets


# -- past answers the documents contradict --------------------------------------


def test_recorded_resolutions_are_flagged_as_unverified(signals: list[Signal]) -> None:
    """Both trap tickets in the pack carry answers that current documents contradict."""
    flagged = {signal.tickets[0] for signal in _of_kind(signals, "unverified_past_answer")}
    assert flagged == {"TKT-450", "TKT-451"}


def test_an_account_with_an_agreement_makes_the_risk_concrete(
    signals: list[Signal],
) -> None:
    stale = next(
        s for s in _of_kind(signals, "unverified_past_answer") if s.tickets == ("TKT-450",)
    )
    assert "signed agreement that may override" in stale.detail


# -- ordering and scope ---------------------------------------------------------


def test_the_most_urgent_signal_comes_first(signals: list[Signal]) -> None:
    assert signals[0].severity == "P1"
    ranks = ["P1", "P2", "P3", "info"]
    positions = [ranks.index(signal.severity) for signal in signals]
    assert positions == sorted(positions)


def test_every_signal_carries_its_evidence(signals: list[Signal]) -> None:
    for signal in signals:
        assert signal.tickets, f"{signal.kind} names no ticket"
        assert signal.accounts, f"{signal.kind} names no account"
        assert signal.detail.strip()


def test_detection_never_widens_what_a_caller_can_see(
    data: OperationalData, chunks: list[Chunk]
) -> None:
    """The same scoped queries as everywhere else: a customer sees only themselves."""
    theirs = detect(data, NORTHSTAR, chunks)

    assert theirs, "a customer with open tickets should still see their own signals"
    for signal in theirs:
        assert set(signal.accounts) <= {"ACCT-001"}, f"{signal.kind} leaked another account"

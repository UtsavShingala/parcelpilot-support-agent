"""A contradiction between sources must be reported, not resolved by ranking alone."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from parcelpilot.ingest.authority import AuthorityTier
from parcelpilot.ingest.documents import Chunk
from parcelpilot.retrieval.conflicts import ConflictKind, detect_conflicts
from parcelpilot.retrieval.scope import AccountScope
from parcelpilot.retrieval.store import DocumentStore

ChunkFactory = Callable[..., Chunk]

_SOP_TEXT = "A booked shipment cancelled after thirty minutes carries a cancellation fee."


def test_an_agreement_and_a_policy_on_one_subject_conflict(make_chunk: ChunkFactory) -> None:
    store = DocumentStore(
        [
            make_chunk("sop", _SOP_TEXT),
            make_chunk(
                "agreement",
                "A booked shipment may be cancelled with no cancellation fee at any time.",
                tier=AuthorityTier.AGREEMENT,
                scope="ACCT-001",
            ),
        ]
    )
    hits = store.search(
        "cancellation fee for a booked shipment", scope=AccountScope.for_accounts("ACCT-001")
    )

    conflict = detect_conflicts(hits)[0]
    assert conflict.governing.chunk.chunk_id == "agreement"
    assert conflict.subordinate.chunk.chunk_id == "sop"
    assert "cancellation" in conflict.shared_terms


def test_explicit_replacement_language_is_reported_as_an_override(
    make_chunk: ChunkFactory,
) -> None:
    store = DocumentStore(
        [
            make_chunk("sop", _SOP_TEXT),
            make_chunk(
                "agreement",
                "This clause replaces the default cancellation fee for a booked shipment.",
                tier=AuthorityTier.AGREEMENT,
                scope="ACCT-001",
            ),
        ]
    )
    hits = store.search(
        "cancellation fee booked shipment", scope=AccountScope.for_accounts("ACCT-001")
    )

    conflict = detect_conflicts(hits)[0]
    assert conflict.kind is ConflictKind.OVERRIDE
    assert "replaces the general terms" in conflict.explanation


def test_an_agreement_that_claims_nothing_is_reported_as_precedence(
    make_chunk: ChunkFactory,
) -> None:
    """Silence about the SOP is still authority, but it is not an explicit override."""
    store = DocumentStore(
        [
            make_chunk("sop", _SOP_TEXT),
            make_chunk(
                "agreement",
                "A booked shipment may be cancelled before pickup with no cancellation fee.",
                tier=AuthorityTier.AGREEMENT,
                scope="ACCT-001",
            ),
        ]
    )
    hits = store.search(
        "cancellation fee booked shipment", scope=AccountScope.for_accounts("ACCT-001")
    )

    conflict = detect_conflicts(hits)[0]
    assert conflict.kind is ConflictKind.PRECEDENCE
    assert "takes precedence over" in conflict.explanation


def test_replacement_language_outside_an_agreement_is_not_an_override(
    make_chunk: ChunkFactory,
) -> None:
    """A policy describing its own history is not asserting authority over a guide."""
    store = DocumentStore(
        [
            make_chunk(
                "sop",
                "This section replaces earlier guidance on cancellation of a booked shipment.",
            ),
            make_chunk(
                "guide",
                "Cancellation of a booked shipment is confirmed by the carrier.",
                tier=AuthorityTier.PRODUCT_DOC,
            ),
        ]
    )
    hits = store.search("cancellation of a booked shipment")

    conflict = detect_conflicts(hits)[0]
    assert conflict.kind is ConflictKind.PRECEDENCE


def test_a_superseded_source_is_named_as_such(make_chunk: ChunkFactory) -> None:
    store = DocumentStore(
        [
            make_chunk("current", "Enterprise P1 first response target is 30 minutes."),
            make_chunk(
                "old",
                "Enterprise P1 first response target is 1 hour.",
                tier=AuthorityTier.DEPRECATED,
            ),
        ]
    )
    hits = store.search("Enterprise P1 first response target", include_deprecated=True)

    conflict = detect_conflicts(hits)[0]
    assert conflict.kind is ConflictKind.SUPERSEDED
    assert "superseded" in conflict.explanation
    assert "explain what changed" in conflict.explanation


def test_sections_of_the_same_document_do_not_conflict(make_chunk: ChunkFactory) -> None:
    first = make_chunk("a", "Cancellation fee for a booked shipment applies.")
    second = make_chunk("b", "Cancellation fee for a booked shipment is waived.")
    same_document = [first, replace(second, source_file=first.source_file)]
    hits = DocumentStore(same_document).search("cancellation fee booked shipment")

    assert detect_conflicts(hits) == []


def test_a_single_shared_term_is_not_a_conflict(make_chunk: ChunkFactory) -> None:
    """Otherwise every passage mentioning "shipment" contradicts every other one."""
    store = DocumentStore(
        [
            make_chunk("sop", "Cancellation of a shipment follows the standard process."),
            make_chunk(
                "agreement",
                "A shipment is collected by a dedicated account manager on request.",
                tier=AuthorityTier.AGREEMENT,
                scope="ACCT-001",
            ),
        ]
    )
    hits = store.search("shipment", scope=AccountScope.for_accounts("ACCT-001"))

    assert detect_conflicts(hits) == []


def test_one_finding_per_document_pair(make_chunk: ChunkFactory) -> None:
    """A five-section agreement against a five-section SOP is one conflict, not twenty-five."""
    text = "Cancellation fee for a booked shipment before pickup."
    chunks = [make_chunk(f"sop-{index}", text) for index in range(3)]
    for index in range(3):
        chunks.append(
            make_chunk(
                f"agreement-{index}", text, tier=AuthorityTier.AGREEMENT, scope="ACCT-001"
            )
        )
    # Give each family a shared source file, as sections of one document would have.
    merged = [
        replace(chunk, source_file=f"{chunk.chunk_id.split('-')[0]}.pdf") for chunk in chunks
    ]
    hits = DocumentStore(merged).search(
        "cancellation fee booked shipment", scope=AccountScope.for_accounts("ACCT-001"), limit=10
    )

    assert len(detect_conflicts(hits)) == 1


def test_no_conflict_is_reported_when_nothing_disagrees(make_chunk: ChunkFactory) -> None:
    hits = DocumentStore([make_chunk("sop", _SOP_TEXT)]).search("cancellation fee")
    assert detect_conflicts(hits) == []


def test_an_agreement_overrides_the_sop_in_the_real_corpus(corpus_dir: Path) -> None:
    """The pack's LumenWorks clause says outright that it replaces the SOP default."""
    store = DocumentStore.from_settings()
    hits = store.search(
        "pickup four hours late carrier at fault service credit",
        scope=AccountScope.for_accounts("ACCT-002"),
    )

    conflict = detect_conflicts(hits)[0]
    assert conflict.kind is ConflictKind.OVERRIDE
    assert conflict.governing.chunk.scope == "ACCT-002"
    assert conflict.subordinate.chunk.tier is AuthorityTier.CURRENT_POLICY


def test_an_account_without_an_agreement_sees_no_conflict(corpus_dir: Path) -> None:
    """Nothing overrides the general policy for a customer who signed no contract."""
    store = DocumentStore.from_settings()
    account_ids = {chunk.scope for chunk in store.chunks if chunk.scope != "global"}
    unscoped = AccountScope.for_accounts("ACCT-UNCONTRACTED")
    assert "ACCT-UNCONTRACTED" not in account_ids

    hits = store.search("P1 first response target", scope=unscoped)
    assert all(conflict.governing.chunk.scope == "global" for conflict in detect_conflicts(hits))

"""Authority must decide ties between relevant passages, and nothing more."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from parcelpilot.ingest.authority import AuthorityTier
from parcelpilot.ingest.documents import Chunk
from parcelpilot.retrieval.store import DocumentStore

ChunkFactory = Callable[..., Chunk]


def test_the_more_authoritative_of_two_equal_matches_wins(make_chunk: ChunkFactory) -> None:
    text = "A booked shipment may be cancelled before pickup with no cancellation fee."
    store = DocumentStore(
        [
            make_chunk("guide", text, tier=AuthorityTier.PRODUCT_DOC),
            make_chunk("agreement", text, tier=AuthorityTier.AGREEMENT),
            make_chunk("policy", text, tier=AuthorityTier.CURRENT_POLICY),
        ]
    )

    ranked = [hit.chunk.chunk_id for hit in store.search("cancellation fee before pickup")]
    assert ranked == ["agreement", "policy", "guide"]


def test_authority_cannot_manufacture_relevance(make_chunk: ChunkFactory) -> None:
    """A senior document that says nothing about the question must not appear."""
    store = DocumentStore(
        [
            make_chunk("agreement", "Dedicated CSM contact details.", tier=AuthorityTier.AGREEMENT),
            make_chunk(
                "guide", "Bulk upload fails above 3,000 rows.", tier=AuthorityTier.PRODUCT_DOC
            ),
        ]
    )

    results = store.search("bulk upload failure")
    assert [hit.chunk.chunk_id for hit in results] == ["guide"]


def test_a_far_better_match_outranks_a_more_authoritative_one(make_chunk: ChunkFactory) -> None:
    store = DocumentStore(
        [
            make_chunk(
                "agreement", "Service credits are capped monthly.", tier=AuthorityTier.AGREEMENT
            ),
            make_chunk(
                "guide",
                "Bulk upload on large CSV files fails intermittently above 3,000 rows.",
                tier=AuthorityTier.PRODUCT_DOC,
            ),
        ]
    )

    top = store.search("bulk upload large CSV rows fails")[0]
    assert top.chunk.chunk_id == "guide"


def test_superseded_material_is_excluded_by_default(make_chunk: ChunkFactory) -> None:
    store = DocumentStore(
        [
            make_chunk("current", "Enterprise P1 target is 30 minutes."),
            make_chunk(
                "old", "Enterprise P1 target is 1 hour.", tier=AuthorityTier.DEPRECATED
            ),
        ]
    )

    assert [hit.chunk.chunk_id for hit in store.search("Enterprise P1 target")] == ["current"]


def test_superseded_material_is_available_when_asked_for(make_chunk: ChunkFactory) -> None:
    """Explaining what changed needs the old text; answering a question does not."""
    store = DocumentStore(
        [
            make_chunk("current", "Enterprise P1 target is 30 minutes."),
            make_chunk(
                "old", "Enterprise P1 target is 1 hour.", tier=AuthorityTier.DEPRECATED
            ),
        ]
    )

    found = store.search("Enterprise P1 target", include_deprecated=True)
    assert {hit.chunk.chunk_id for hit in found} == {"current", "old"}
    assert found[0].chunk.chunk_id == "current", "the superseded version must not lead"


def test_historical_material_ranks_below_current_guidance(make_chunk: ChunkFactory) -> None:
    text = "A cancellation fee of INR 250 applies after thirty minutes."
    store = DocumentStore(
        [
            make_chunk("ticket", text, tier=AuthorityTier.HISTORICAL),
            make_chunk("sop", text, tier=AuthorityTier.CURRENT_POLICY),
        ]
    )

    assert [hit.chunk.chunk_id for hit in store.search("cancellation fee")] == ["sop", "ticket"]


def test_hits_explain_themselves(make_chunk: ChunkFactory) -> None:
    store = DocumentStore([make_chunk("sop", "Cancellation of a booked shipment incurs a fee.")])
    hit = store.search("cancellation fee")[0]

    assert 0.0 < hit.lexical_score <= 1.0
    assert hit.authority_multiplier == 1.20
    assert hit.score == hit.lexical_score * hit.authority_multiplier
    assert set(hit.matched_terms) == {"cancellation", "fee"}
    assert hit.citation == hit.chunk.citation


def test_the_limit_is_respected(make_chunk: ChunkFactory) -> None:
    store = DocumentStore(
        [make_chunk(f"c{index}", "cancellation fee applies") for index in range(10)]
    )
    assert len(store.search("cancellation fee", limit=3)) == 3


def test_an_unmatched_query_returns_nothing(make_chunk: ChunkFactory) -> None:
    store = DocumentStore([make_chunk("sop", "Cancellation terms.")])
    assert store.search("quantum chromodynamics") == []


def test_an_empty_store_is_searchable() -> None:
    store = DocumentStore([])
    assert len(store) == 0
    assert store.search("anything") == []
    assert store.get("missing") is None


def test_chunks_are_addressable_by_id(make_chunk: ChunkFactory) -> None:
    chunk = make_chunk("sop", "Cancellation terms.")
    store = DocumentStore([chunk])
    assert store.get("sop") == chunk
    assert store.chunks == [chunk]


def test_the_real_corpus_never_answers_from_the_superseded_policy(corpus_dir: Path) -> None:
    """The pack's v2 policy is a near-copy of v3, so it matches well and must be filtered."""
    store = DocumentStore.from_settings()
    questions = [
        "Enterprise P1 first response target",
        "Growth plan P2 response time",
        "what are the severity definitions",
    ]
    for question in questions:
        assert not any(hit.chunk.is_deprecated for hit in store.search(question, limit=25))

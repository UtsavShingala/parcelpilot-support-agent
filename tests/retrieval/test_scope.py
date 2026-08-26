"""One customer must never reach another customer's agreement, however they ask."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from parcelpilot.ingest.authority import GLOBAL_SCOPE, AuthorityTier
from parcelpilot.ingest.documents import Chunk
from parcelpilot.retrieval.scope import AccountScope
from parcelpilot.retrieval.store import DocumentStore

ChunkFactory = Callable[..., Chunk]


def test_general_material_is_visible_to_everyone() -> None:
    assert AccountScope.none().permits(GLOBAL_SCOPE)
    assert AccountScope.for_accounts("ACCT-001").permits(GLOBAL_SCOPE)
    assert AccountScope.unrestricted_access().permits(GLOBAL_SCOPE)


def test_private_material_needs_an_entitlement() -> None:
    scope = AccountScope.for_accounts("ACCT-001")
    assert scope.permits("ACCT-001")
    assert not scope.permits("ACCT-002")


def test_the_default_scope_denies_all_private_material() -> None:
    """Code that forgets to pass a caller context must see less, not more."""
    assert not AccountScope.none().permits("ACCT-001")
    assert not AccountScope.none()


def test_an_internal_scope_reaches_every_account() -> None:
    scope = AccountScope.unrestricted_access()
    assert scope.permits("ACCT-001")
    assert scope.permits("ACCT-999")
    assert scope.describe() == "all accounts"


def _two_customer_store(make_chunk: ChunkFactory) -> DocumentStore:
    terms = "Cancellation of a booked shipment before pickup carries no fee."
    return DocumentStore(
        [
            make_chunk("policy", terms, tier=AuthorityTier.CURRENT_POLICY),
            make_chunk("first", terms, tier=AuthorityTier.AGREEMENT, scope="ACCT-001"),
            make_chunk("second", terms, tier=AuthorityTier.AGREEMENT, scope="ACCT-002"),
        ]
    )


def test_a_customer_sees_their_own_agreement_and_general_policy(
    make_chunk: ChunkFactory,
) -> None:
    store = _two_customer_store(make_chunk)
    found = store.search(
        "cancellation fee before pickup", scope=AccountScope.for_accounts("ACCT-001")
    )
    assert {hit.chunk.chunk_id for hit in found} == {"policy", "first"}


def test_a_customer_never_sees_another_account_even_when_it_ranks_highest(
    make_chunk: ChunkFactory,
) -> None:
    """The excluded agreement would otherwise be the top hit, being equally relevant."""
    store = _two_customer_store(make_chunk)
    unrestricted = store.search(
        "cancellation fee before pickup", scope=AccountScope.unrestricted_access()
    )
    assert "second" in {hit.chunk.chunk_id for hit in unrestricted}

    found = store.search(
        "cancellation fee before pickup", scope=AccountScope.for_accounts("ACCT-001")
    )
    assert all(hit.chunk.scope != "ACCT-002" for hit in found)


def test_searching_without_a_scope_returns_only_general_material(
    make_chunk: ChunkFactory,
) -> None:
    store = _two_customer_store(make_chunk)
    assert [hit.chunk.chunk_id for hit in store.search("cancellation fee")] == ["policy"]


def test_excluded_material_does_not_occupy_a_result_slot(make_chunk: ChunkFactory) -> None:
    """Filtering happens before the limit, so scoping never costs a caller results."""
    store = DocumentStore(
        [
            make_chunk("other", "cancellation fee", tier=AuthorityTier.AGREEMENT, scope="ACCT-002"),
            make_chunk("policy-a", "cancellation fee applies"),
            make_chunk("policy-b", "cancellation fee waived"),
        ]
    )
    found = store.search(
        "cancellation fee", scope=AccountScope.for_accounts("ACCT-001"), limit=2
    )
    assert {hit.chunk.chunk_id for hit in found} == {"policy-a", "policy-b"}


def test_visible_chunks_reports_exactly_what_a_caller_can_reach(
    make_chunk: ChunkFactory,
) -> None:
    store = _two_customer_store(make_chunk)
    scope = AccountScope.for_accounts("ACCT-002")
    assert {chunk.chunk_id for chunk in store.visible_chunks(scope)} == {"policy", "second"}


def test_no_question_reaches_another_account_in_the_real_corpus(corpus_dir: Path) -> None:
    """Swept across every account, no phrasing may surface someone else's contract."""
    store = DocumentStore.from_settings()
    account_ids = sorted(
        {chunk.scope for chunk in store.chunks if chunk.scope != GLOBAL_SCOPE}
    )
    assert len(account_ids) > 1, "the corpus needs two scoped accounts for this to mean anything"

    questions = [
        "cancellation fee for a booked shipment",
        "service credit for a late pickup",
        "P1 first response target",
        "who is my dedicated account manager",
        "what does my agreement say",
    ]
    for account_id in account_ids:
        scope = AccountScope.for_accounts(account_id)
        for question in questions:
            for hit in store.search(question, scope=scope, limit=25):
                assert hit.chunk.scope in {GLOBAL_SCOPE, account_id}, (
                    f"{account_id} reached {hit.chunk.chunk_id} via {question!r}"
                )

"""Identifiers must stay precise without losing the people who write them loosely."""

from __future__ import annotations

from parcelpilot.retrieval.lexical import LexicalIndex
from parcelpilot.retrieval.text import content_terms, tokenize


def test_identifiers_are_indexed_whole_and_in_parts() -> None:
    assert tokenize("KI-208 affects ACCT-002") == [
        "ki-208",
        "ki",
        "208",
        "affects",
        "acct-002",
        "acct",
        "002",
    ]


def test_thousands_separators_do_not_split_numbers() -> None:
    assert tokenize("credits above INR 1,000") == ["credits", "above", "inr", "1000"]


def test_a_comma_between_terms_still_separates_them() -> None:
    assert tokenize("P1, P2") == ["p1", "p2"]


def test_content_terms_drop_filler_and_single_characters() -> None:
    assert content_terms("What is the fee for a cancellation?") == {"fee", "cancellation"}


def test_the_exact_identifier_outranks_a_passage_that_merely_shares_a_number() -> None:
    index = LexicalIndex(
        [
            "KI-208 covers bulk upload failures on large CSV files.",
            "The limit is 208 rows per batch under the old plan.",
        ]
    )
    exact, incidental = index.scores("KI-208")
    assert exact > incidental


def test_a_loosely_written_identifier_still_matches() -> None:
    index = LexicalIndex(
        [
            "KI-208 covers bulk upload failures.",
            "Pickup windows are confirmed by the carrier.",
        ]
    )
    known_issue, unrelated = index.scores("issue 208")
    assert known_issue > unrelated


def test_scores_are_normalised_to_the_best_match() -> None:
    index = LexicalIndex(["cancellation fee applies", "unrelated text about pickups"])
    scores = index.scores("cancellation fee")
    assert max(scores) == 1.0
    assert all(0.0 <= score <= 1.0 for score in scores)


def test_an_unmatched_query_scores_everything_zero() -> None:
    index = LexicalIndex(["cancellation fee applies", "pickup windows"])
    assert index.scores("quantum chromodynamics") == [0.0, 0.0]


def test_a_term_in_most_of_the_corpus_still_carries_signal() -> None:
    """Pins the IDF choice: Okapi's formula zeroes out terms this common."""
    index = LexicalIndex(
        [
            "cancellation terms and cancellation fees",
            "cancellation is possible",
            "cancellation applies here",
            "pickup windows only",
        ]
    )
    scores = index.scores("cancellation")
    assert scores[0] > scores[3]
    assert scores[0] > 0.0


def test_an_empty_index_does_not_raise() -> None:
    index = LexicalIndex([])
    assert len(index) == 0
    assert index.scores("anything") == []


def test_an_index_of_empty_documents_does_not_raise() -> None:
    index = LexicalIndex(["", ""])
    assert index.scores("anything") == [0.0, 0.0]


def test_an_empty_query_scores_everything_zero() -> None:
    index = LexicalIndex(["cancellation fee applies"])
    assert index.scores("   ") == [0.0]


def test_a_passage_sharing_no_terms_with_the_query_scores_zero() -> None:
    """The relevance gate depends on this: BM25+ gives every passage a floor instead."""
    index = LexicalIndex(
        ["bulk upload fails on large files", "dedicated account manager contact details"]
    )
    matching, unrelated = index.scores("bulk upload")
    assert matching > 0.0
    assert unrelated == 0.0

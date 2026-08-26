"""Authority is read from the document, not from the filename around it."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

from parcelpilot.ingest.authority import (
    GLOBAL_SCOPE,
    AuthorityTier,
    derive_authority,
    parse_header,
)
from parcelpilot.ingest.sections import parse_pdf


def test_header_values_may_contain_capitals() -> None:
    """A shouty value must not be mistaken for the next key."""
    fields = parse_header(
        "Status: DEPRECATED - DO NOT USE FOR CURRENT REQUESTS "
        "Effective: 1 January 2025 Superseded by: Support Policy v3"
    )
    assert fields == {
        "status": "DEPRECATED - DO NOT USE FOR CURRENT REQUESTS",
        "effective": "1 January 2025",
        "superseded by": "Support Policy v3",
    }


def test_a_superseded_document_is_deprecated_even_when_it_claims_to_be_current() -> None:
    authority = derive_authority(
        title="Handbook v2", header="Status: CURRENT Superseded by: Handbook v3"
    )
    assert authority.tier is AuthorityTier.DEPRECATED
    assert authority.is_deprecated
    assert authority.superseded_by == "Handbook v3"


def test_a_deprecation_notice_in_the_status_is_enough() -> None:
    authority = derive_authority(title="Handbook v2", header="Status: DEPRECATED - DO NOT USE")
    assert authority.tier is AuthorityTier.DEPRECATED
    assert authority.status == "deprecated"


def test_an_agreement_is_scoped_to_the_account_that_signed_it() -> None:
    authority = derive_authority(
        title="Acme Enterprise Agreement",
        header="Account: ACCT-007 Customer: Acme Term: 1 January 2026 to 31 December 2026 "
        "Status: ACTIVE",
    )
    assert authority.tier is AuthorityTier.AGREEMENT
    assert authority.scope == "ACCT-007"
    assert authority.is_account_scoped
    assert (authority.term_start, authority.term_end) == (date(2026, 1, 1), date(2026, 12, 31))


def test_an_active_agreement_is_not_read_as_deprecated() -> None:
    """Agreements say "Status: ACTIVE"; only deprecation markers may demote a document."""
    authority = derive_authority(
        title="Acme Agreement", header="Account: ACCT-007 Status: ACTIVE"
    )
    assert authority.tier is AuthorityTier.AGREEMENT


def test_policies_and_sops_outrank_product_documentation() -> None:
    policy = derive_authority(title="Support Policy v3", header="Status: CURRENT")
    sop = derive_authority(title="Cancellation SOP v4", header="Status: CURRENT")
    guide = derive_authority(title="Product Operations Guide", header="Status: CURRENT")

    assert policy.tier is AuthorityTier.CURRENT_POLICY
    assert sop.tier is AuthorityTier.CURRENT_POLICY
    assert guide.tier is AuthorityTier.PRODUCT_DOC
    assert guide.tier > policy.tier  # higher number, weaker claim
    assert (policy.version, sop.version, guide.version) == ("v3", "v4", None)


def test_effective_and_updated_are_read_interchangeably() -> None:
    effective = derive_authority(title="A", header="Status: CURRENT Effective: 15 June 2026")
    updated = derive_authority(title="B", header="Status: CURRENT Updated: 14 August 2026")
    assert effective.effective_date == date(2026, 6, 15)
    assert updated.effective_date == date(2026, 8, 14)


def test_a_document_without_a_header_still_classifies() -> None:
    authority = derive_authority(title="Untitled", header="")
    assert authority.tier is AuthorityTier.PRODUCT_DOC
    assert authority.scope == GLOBAL_SCOPE
    assert authority.effective_date is None


def test_authority_survives_renaming_the_file(tmp_path: Path, pdf_paths: list[Path]) -> None:
    """The design claim: metadata comes from the document, so filenames may change."""
    for index, path in enumerate(pdf_paths):
        anonymous = tmp_path / f"{index}.pdf"
        shutil.copy(path, anonymous)

        original = parse_pdf(path)
        renamed = parse_pdf(anonymous)

        assert derive_authority(
            title=original.title, header=original.header
        ) == derive_authority(title=renamed.title, header=renamed.header)


def test_the_pack_contains_scoped_agreements_and_a_deprecated_policy(
    pdf_paths: list[Path],
) -> None:
    """The traps the corpus was built around must actually be detected."""
    authorities = [
        derive_authority(title=(parsed := parse_pdf(path)).title, header=parsed.header)
        for path in pdf_paths
    ]
    tiers = {authority.tier for authority in authorities}

    assert AuthorityTier.DEPRECATED in tiers, "the superseded policy was not detected"
    assert AuthorityTier.AGREEMENT in tiers, "no customer agreement was detected"
    assert all(
        authority.is_account_scoped
        for authority in authorities
        if authority.tier is AuthorityTier.AGREEMENT
    ), "an agreement was left globally scoped"
    assert all(
        not authority.is_account_scoped
        for authority in authorities
        if authority.tier is not AuthorityTier.AGREEMENT
    ), "a general document was scoped to one account"

"""Chunk factory for retrieval tests, so cases can be stated in one line."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from parcelpilot.ingest.authority import GLOBAL_SCOPE, AuthorityTier, DocumentAuthority
from parcelpilot.ingest.documents import Chunk

_DOC_TYPE = {
    AuthorityTier.AGREEMENT: "agreement",
    AuthorityTier.CURRENT_POLICY: "policy",
    AuthorityTier.PRODUCT_DOC: "guide",
    AuthorityTier.HISTORICAL: "ticket",
    AuthorityTier.DEPRECATED: "policy",
}

ChunkFactory = Callable[..., Chunk]


def _make_chunk(
    chunk_id: str,
    text: str,
    *,
    tier: AuthorityTier = AuthorityTier.CURRENT_POLICY,
    scope: str = GLOBAL_SCOPE,
    heading: str = "1. Section",
    doc_title: str | None = None,
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        text=text,
        source_file=f"{chunk_id}.pdf",
        doc_title=doc_title or f"Document {chunk_id}",
        heading_path=(heading,),
        section_number=None,
        page=1,
        authority=DocumentAuthority(
            doc_type=_DOC_TYPE[tier],
            tier=tier,
            status="deprecated" if tier is AuthorityTier.DEPRECATED else "current",
            scope=scope,
        ),
    )


@pytest.fixture
def make_chunk() -> ChunkFactory:
    return _make_chunk

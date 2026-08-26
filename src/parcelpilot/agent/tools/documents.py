"""Document search, returning conflicts alongside results.

Conflicts are attached to the search result rather than left for the model to
notice. When a customer's agreement and the general SOP both answer a question, the
override is the single most important fact in the result set and the easiest to
read past -- both passages sound authoritative in isolation. Surfacing it as
structured data makes it something the agent has to address rather than something
it might mention.

Superseded material is off by default and has to be asked for. The tool description
says why, because the model is the one deciding whether to ask.
"""

from __future__ import annotations

from typing import Any

from parcelpilot.agent.tools.base import (
    ALL_ROLES,
    Tool,
    boolean_field,
    integer_field,
    object_schema,
    string_field,
)
from parcelpilot.auth.context import CallerContext
from parcelpilot.retrieval.conflicts import detect_conflicts
from parcelpilot.retrieval.store import DocumentStore, RetrievedChunk

DESCRIPTION = """\
Search ParcelPilot's policies, SOPs, product documentation and customer agreements.

This is the only source of policy truth. Answer from what it returns; do not rely on
general knowledge about logistics, refunds or support practice.

Results carry an authority tier. A signed customer agreement outranks current
policy, which outranks product documentation. Where two sources disagree, the
`conflicts` field names which governs and why -- say so in your answer and cite
both, rather than quietly reporting only the winner.

Superseded documents are excluded unless `include_superseded` is set. Ask for them
only to explain that something changed, never to answer what the rule is now.\
"""


def build_search_documents(store: DocumentStore) -> Tool:
    def search_documents(
        caller: CallerContext,
        *,
        query: str,
        limit: int = 6,
        include_superseded: bool = False,
    ) -> dict[str, Any]:
        hits = store.search(
            query,
            scope=caller.account_scope(),
            limit=limit,
            include_deprecated=include_superseded,
        )
        conflicts = detect_conflicts(hits)

        return {
            "query": query,
            "visible_scope": caller.account_scope().describe(),
            "result_count": len(hits),
            "results": [_render(hit) for hit in hits],
            "conflicts": [
                {
                    "kind": conflict.kind.value,
                    "explanation": conflict.explanation,
                    "governing": conflict.governing.chunk.citation,
                    "subordinate": conflict.subordinate.chunk.citation,
                }
                for conflict in conflicts
            ],
            "note": (
                "Nothing in the supplied documents matched this query. Do not answer "
                "from general knowledge; try different wording or escalate."
                if not hits
                else None
            ),
        }

    return Tool(
        name="search_documents",
        description=DESCRIPTION,
        parameters=object_schema(
            {
                "query": string_field(
                    "What to search for. Use the words the policy would use, such as "
                    "'cancellation fee', 'failed pickup service credit', "
                    "'first response target'."
                ),
                "limit": integer_field("How many passages to return.", minimum=1, maximum=15),
                "include_superseded": boolean_field(
                    "Include withdrawn or replaced documents. Only for explaining what "
                    "changed; never for deciding what the current rule is."
                ),
            },
            required=["query"],
        ),
        handler=search_documents,
        roles=ALL_ROLES,
    )


def _render(hit: RetrievedChunk) -> dict[str, Any]:
    chunk = hit.chunk
    return {
        "citation": chunk.citation,
        "source_file": chunk.source_file,
        "authority_tier": chunk.tier.name,
        "applies_to": chunk.scope,
        "status": chunk.authority.status,
        "effective_date": (
            chunk.authority.effective_date.isoformat()
            if chunk.authority.effective_date
            else None
        ),
        "text": chunk.text,
        "relevance": round(hit.lexical_score, 3),
        "matched_terms": list(hit.matched_terms),
    }

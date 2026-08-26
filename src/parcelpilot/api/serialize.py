"""Agent events on the wire.

The loop's event types are the contract; this only renders them. Keeping the
mapping here rather than on the dataclasses means the agent never acquires an
opinion about JSON, and a second transport could render the same events
differently without touching the loop.

Search payloads are trimmed to the fields a citation card shows. The chunk text
is kept -- a reviewer should be able to read the clause an answer rests on without
opening the PDF -- but relevance internals and matched-term lists are not sent,
since nothing in the interface reads them.
"""

from __future__ import annotations

import json
from typing import Any

from parcelpilot.agent.events import AgentEvent, ToolFinished

# Fields of a search result that reach the browser, in the order a card shows them.
CITATION_FIELDS = (
    "citation",
    "source_file",
    "version",
    "clause",
    "authority_tier",
    "applies_to",
    "status",
    "effective_date",
    "text",
)

MAX_ROWS = 25
"""Cap on structured rows forwarded per tool result, so one broad lookup cannot
push a megabyte of JSON into the browser."""


def event_to_dict(event: AgentEvent) -> dict[str, Any]:
    """Render one event as the object the browser receives."""
    payload: dict[str, Any] = {"type": event.type}

    for name in (
        "name",
        "step",
        "ok",
        "summary",
        "error",
        "mutating",
        "text",
        "final",
        # Without this a browser cannot tell two calls in one reply apart: they share
        # a step, so matching on step alone gives both cards the first result.
        "call_id",
    ):
        if hasattr(event, name):
            payload[name] = getattr(event, name)

    if hasattr(event, "arguments"):
        payload["arguments"] = event.arguments
    if hasattr(event, "reason"):
        payload["reason"] = event.reason
    if hasattr(event, "detail"):
        payload["detail"] = event.detail
    if hasattr(event, "steps"):
        payload["steps"] = event.steps
    if hasattr(event, "escalated"):
        payload["escalated"] = event.escalated
    if hasattr(event, "message"):
        payload["message"] = event.message

    draft = getattr(event, "draft", None)
    if draft is not None:
        payload["draft"] = draft.to_dict()

    if isinstance(event, ToolFinished) and event.payload is not None:
        payload["result"] = _trim(event.payload)

    return payload


def sse(event: AgentEvent) -> str:
    """One server-sent event frame.

    The event name is on the wire as well as in the data so a client can attach
    per-type listeners instead of switching on a field it has to parse first.
    """
    body = json.dumps(event_to_dict(event), default=str)
    return f"event: {event.type}\ndata: {body}\n\n"


def sse_error(message: str, *, kind: str = "failed") -> str:
    body = json.dumps({"type": kind, "message": message})
    return f"event: {kind}\ndata: {body}\n\n"


def _trim(payload: Any) -> Any:
    """Forward what an interface renders, and leave the rest behind."""
    if not isinstance(payload, dict):
        return payload

    trimmed: dict[str, Any] = {
        key: value
        for key, value in payload.items()
        if key not in {"results", "orders", "tickets", "accounts"}
    }

    if isinstance(payload.get("results"), list):
        trimmed["results"] = [
            {field: item.get(field) for field in CITATION_FIELDS if field in item}
            for item in payload["results"][:MAX_ROWS]
        ]
    for rows in ("orders", "tickets", "accounts"):
        if isinstance(payload.get(rows), list):
            trimmed[rows] = payload[rows][:MAX_ROWS]

    return trimmed


__all__ = ["CITATION_FIELDS", "event_to_dict", "sse", "sse_error"]

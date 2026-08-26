"""Confirming a prepared action: the human half of the two-phase split.

The model can prepare. Only this endpoint can execute, and the model cannot reach
it -- it is not a tool, it is not in the registry, and no prompt can cause it to
fire. The only thing that reaches it is a person pressing a button.

The request carries a draft id and nothing else. The draft itself is fetched from
the server-side session, so a browser cannot confirm an action it invented, edit
the amount on one the model prepared, or replay a draft belonging to a different
visitor. Authorisation is then re-checked inside the ledger against the caller,
because a draft is data and the role that may prepare an action is not always the
role that may perform it.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from parcelpilot.agent.tools.base import ToolPermissionError
from parcelpilot.api.dependencies import current_session, runtime_of
from parcelpilot.api.errors import forbidden, not_found
from parcelpilot.auth.context import Role

router = APIRouter(tags=["actions"])


class Confirmation(BaseModel):
    """What the browser may say. Note what is absent: the action itself."""

    draft_id: str = Field(min_length=1, max_length=128)


@router.post("/actions/confirm")
def confirm(body: Confirmation, request: Request) -> dict[str, object]:
    runtime = runtime_of(request)
    session = current_session(request)

    draft = session.draft(body.draft_id)
    if draft is None:
        raise not_found(
            "no such draft is awaiting confirmation in this session; "
            "ask again and confirm the draft the assistant prepares"
        )

    with runtime.ledger() as ledger:
        already = ledger.find(draft.draft_id)
        try:
            record = ledger.confirm(draft, session.caller)
        except ToolPermissionError as denied:
            raise forbidden(str(denied)) from denied

    return {
        "status": "already recorded" if already else "confirmed",
        "action": record.to_dict(),
    }


@router.get("/actions")
def history(request: Request) -> dict[str, object]:
    """Actions this caller is allowed to see. Customers see only their own."""
    runtime = runtime_of(request)
    session = current_session(request)
    with runtime.ledger() as ledger:
        records = ledger.records(session.caller)
    return {
        "actions": [record.to_dict() for record in records],
        "role": session.caller.role.value,
        "internal": session.caller.role in {Role.SUPPORT_AGENT, Role.OPS_MANAGER},
    }


__all__ = ["router"]

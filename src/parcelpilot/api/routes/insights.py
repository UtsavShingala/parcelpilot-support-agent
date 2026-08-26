"""The operations view: signals, and the escalations people confirmed.

Restricted to internal roles. Not because a customer would see another account --
the detectors run through the same scoped queries as everything else, so a customer
would only ever see their own tickets -- but because this is a staff tool, and
answering it for customers would invite them to read it as a service commitment.
The refusal is a role check here, and the scoping underneath it is a second,
independent guarantee.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from parcelpilot.api.dependencies import current_session, runtime_of
from parcelpilot.api.errors import forbidden
from parcelpilot.insights.detect import detect

router = APIRouter(tags=["insights"])


@router.get("/insights")
def operations_view(request: Request) -> dict[str, object]:
    runtime = runtime_of(request)
    session = current_session(request)
    caller = session.caller

    if not caller.is_internal:
        raise forbidden(
            "the operations view is for ParcelPilot staff; "
            "ask a question instead and the assistant will answer for your account"
        )

    signals = detect(runtime.data, caller, runtime.chunks)
    with runtime.ledger() as ledger:
        records = ledger.records(caller)

    return {
        "snapshot_at": runtime.snapshot_at.isoformat(),
        "scope": caller.account_scope().describe(),
        "signals": [signal.to_dict() for signal in signals],
        "counts": _counts(signals),
        "escalations": [record.to_dict() for record in records],
    }


def _counts(signals: list) -> dict[str, int]:  # noqa: ANN401 - Signal, kept loose for JSON
    tally: dict[str, int] = {}
    for signal in signals:
        tally[signal.severity] = tally.get(signal.severity, 0) + 1
    return tally


__all__ = ["router"]

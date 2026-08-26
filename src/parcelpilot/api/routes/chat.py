"""Asking a question, streamed as it happens.

The loop already yields typed events; this forwards them as they arrive rather than
waiting for a finished answer. A turn runs several tool calls and takes seconds,
and a spinner that says nothing for eight seconds is a worse interface than one
that says "searching the agreements" while it does exactly that.

Two things are enforced here and nowhere else in the request path:

**A persona must already be established.** The question carries no identity. Who is
asking comes from the session cookie, so a request cannot name an account it would
like to be treated as.

**The session's message allowance is claimed before the work starts.** A run that
fails still costs tokens, so charging only for successes would leave a public demo
drainable by anyone able to provoke failures.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from parcelpilot.agent.events import ActionDrafted, Completed
from parcelpilot.api.dependencies import current_session, runtime_of
from parcelpilot.api.errors import rate_limited
from parcelpilot.api.runtime import Runtime
from parcelpilot.api.serialize import sse, sse_error
from parcelpilot.api.sessions import ChatSession, SessionLimitReached

router = APIRouter(tags=["chat"])

MAX_QUESTION_CHARS = 2000


class Ask(BaseModel):
    """A question. Deliberately the only thing a client may send."""

    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)


@router.post("/chat")
def chat(body: Ask, request: Request) -> StreamingResponse:
    runtime = runtime_of(request)
    session = current_session(request)

    try:
        remaining = runtime.sessions.spend_message(session)
    except SessionLimitReached as limit:
        raise rate_limited(str(limit)) from limit

    return StreamingResponse(
        _stream(runtime, session, body.question.strip(), remaining),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Proxies that buffer will hold the whole turn and defeat the point.
            "X-Accel-Buffering": "no",
        },
    )


def _stream(
    runtime: Runtime, session: ChatSession, question: str, remaining: int
) -> Iterator[str]:
    """Forward the loop's events, remembering any drafts it prepares."""
    yield sse_error(
        f"{remaining} message(s) left in this session", kind="session_status"
    )
    answer = ""
    try:
        for event in runtime.agent.run(session.caller, question, history=session.history):
            if isinstance(event, ActionDrafted):
                # Held server-side so confirmation names a draft we already know,
                # rather than accepting one the browser composed.
                session.remember(event.draft)
            if isinstance(event, Completed):
                answer = event.text
            yield sse(event)
    except Exception as error:  # noqa: BLE001 - the stream must not die silently
        yield sse_error(f"the assistant stopped unexpectedly: {error}")
    finally:
        if answer:
            session.remember_exchange(question, answer)


__all__ = ["router"]

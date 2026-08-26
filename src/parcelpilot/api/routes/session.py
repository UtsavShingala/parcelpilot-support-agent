"""Persona selection: the mock login.

Signing in exchanges a persona id for an opaque session id, set as an httpOnly
cookie. That id is the browser's only credential from then on.

What the browser never receives is the caller's role or account id. It could not be
trusted with them anyway -- anything the client holds, the client can edit -- so
rather than sending them and re-validating on every endpoint, they simply never
leave the server. There is no claim to forge because there is no claim.

The roster itself is public: a login screen has to show what you can sign in as.
It carries only a label and a plan, never contract terms, contact names or notes.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from parcelpilot.api.dependencies import current_session, runtime_of
from parcelpilot.api.errors import bad_request, not_found
from parcelpilot.api.runtime import Runtime
from parcelpilot.api.sessions import SESSION_COOKIE, ChatSession

router = APIRouter(tags=["session"])


class SignIn(BaseModel):
    persona_id: str = Field(min_length=1, max_length=64)


@router.get("/personas")
def list_personas(request: Request) -> dict[str, object]:
    """The sign-in roster, as a picker needs it."""
    runtime = runtime_of(request)
    return {
        "personas": [persona.to_public_dict() for persona in runtime.personas],
        "snapshot_at": runtime.snapshot_at.isoformat(),
        "mode": runtime.mode,
        "mode_description": runtime.mode_description,
    }


@router.post("/session")
def sign_in(body: SignIn, request: Request, response: Response) -> dict[str, object]:
    """Exchange a persona id for a session."""
    runtime = runtime_of(request)
    persona = runtime.persona(body.persona_id)
    if persona is None:
        known = ", ".join(item.persona_id for item in runtime.personas)
        raise not_found(f"unknown persona {body.persona_id!r}; expected one of: {known}")

    session = runtime.sessions.create(persona)
    response.set_cookie(
        SESSION_COOKIE,
        session.session_id,
        httponly=True,
        samesite="lax",
        # The app is served from the same origin as the API, so the cookie needs no
        # cross-site relaxation. Secure is left to the deployment: it must be on
        # behind TLS, and would break a plain-HTTP local run.
        path="/",
    )
    # Described from the session just created, not from the request: the cookie
    # carrying it has not reached the browser yet, so there is nothing to read back.
    return _describe(runtime, session)


@router.get("/session")
def whoami(request: Request) -> dict[str, object]:
    """Who the caller is signed in as, for restoring a reloaded page."""
    return _describe(runtime_of(request), current_session(request))


@router.delete("/session")
def sign_out(request: Request, response: Response) -> dict[str, str]:
    runtime = runtime_of(request)
    runtime.sessions.end(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "signed out"}


def _describe(runtime: Runtime, session: ChatSession) -> dict[str, object]:
    return {
        "persona": session.persona.to_public_dict(),
        "messages_remaining": runtime.sessions.remaining(session),
        "messages_allowed": runtime.sessions.max_messages,
        "snapshot_at": runtime.snapshot_at.isoformat(),
        "mode": runtime.mode,
        "mode_description": runtime.mode_description,
    }


__all__ = ["bad_request", "router"]

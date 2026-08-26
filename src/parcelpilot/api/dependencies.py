"""Resolving a request to the caller behind it.

Every authenticated route goes through :func:`current_session`. There is one way
to learn who is asking, it reads the cookie and nothing else, and it refuses
rather than defaulting. A route that forgets to call it gets no caller at all,
which fails closed.
"""

from __future__ import annotations

from fastapi import Request

from parcelpilot.api.errors import no_session
from parcelpilot.api.runtime import Runtime
from parcelpilot.api.sessions import SESSION_COOKIE, ChatSession


def runtime_of(request: Request) -> Runtime:
    runtime: Runtime | None = getattr(request.app.state, "runtime", None)
    if runtime is None:  # pragma: no cover - set during startup
        raise RuntimeError("the application was started without a runtime")
    return runtime


def current_session(request: Request) -> ChatSession:
    """The signed-in session, or a 401.

    The account and role are read from the server-side session, never from the
    request body or a header. A client cannot ask to be somebody else because it
    has no way to say who it is beyond presenting its cookie.
    """
    session = runtime_of(request).sessions.get(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise no_session()
    return session


__all__ = ["current_session", "runtime_of"]

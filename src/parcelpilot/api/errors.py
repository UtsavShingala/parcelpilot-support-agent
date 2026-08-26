"""HTTP failures, phrased for whoever has to act on them.

An error a visitor sees should say what happened and what to do; an error a
developer sees should not require reading the traceback to work out which
precondition failed. These wrappers exist so both come out consistently and so no
route invents its own status-code convention.
"""

from __future__ import annotations

from fastapi import HTTPException, status


def bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def no_session() -> HTTPException:
    """No persona has been chosen, so there is nobody to answer as.

    Deliberately 401 rather than 403: the caller is unauthenticated, not forbidden,
    and the interface should send them back to the picker rather than tell them
    they lack permission.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="choose a persona before asking a question",
    )


def forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def rate_limited(detail: str) -> HTTPException:
    """This session has spent its allowance.

    429 rather than 403 so a client can tell "come back later or start again" from
    "you may never do this".
    """
    return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail)


__all__ = ["bad_request", "forbidden", "no_session", "not_found", "rate_limited"]

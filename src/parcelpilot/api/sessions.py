"""Server-side sessions.

The browser holds one thing: an opaque, unguessable session id. Everything that
decides what a caller may read -- role, account, the tools they are offered -- lives
here and is looked up by that id. Nothing about authority is ever sent to the
browser, and nothing about authority is ever read back from it.

That is a deliberate shape. If the client held its own account id, every endpoint
would have to re-derive trust from a value the client could edit, and one endpoint
forgetting to would be a cross-account leak. Here there is nothing to forget: an
unknown id is an unknown caller, and an unknown caller gets nothing.

Prepared action drafts live on the session too. They exist between the model
proposing something and a person confirming it, which is exactly one session's
lifetime -- and holding them server-side means a confirmation names a draft the
server already knows rather than submitting one the browser composed.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field

from parcelpilot.agent.model import Message
from parcelpilot.agent.tools.actions import ActionDraft
from parcelpilot.auth.context import CallerContext
from parcelpilot.auth.personas import Persona

SESSION_COOKIE = "pp_session"

# Long enough that guessing is hopeless; the cookie is httpOnly so nothing in the
# page can read it either.
TOKEN_BYTES = 32


class SessionLimitReached(RuntimeError):
    """A session has used its allowance of messages."""


@dataclass
class ChatSession:
    """One signed-in visitor."""

    session_id: str
    persona: Persona
    messages_used: int = 0
    history: list[Message] = field(default_factory=list)
    drafts: dict[str, ActionDraft] = field(default_factory=dict)

    @property
    def caller(self) -> CallerContext:
        return self.persona.context

    def remember(self, draft: ActionDraft) -> None:
        self.drafts[draft.draft_id] = draft

    def draft(self, draft_id: str) -> ActionDraft | None:
        return self.drafts.get(draft_id)


class SessionStore:
    """In-memory session table.

    In-memory is the right scope for a demo: sessions are cheap, disposable, and
    nothing of value is lost by a restart. A real deployment would move this to
    Redis, which changes this class and nothing that uses it.
    """

    def __init__(self, *, max_messages: int) -> None:
        self._sessions: dict[str, ChatSession] = {}
        self._lock = threading.Lock()
        self._max_messages = max_messages

    @property
    def max_messages(self) -> int:
        return self._max_messages

    def create(self, persona: Persona) -> ChatSession:
        session = ChatSession(session_id=secrets.token_urlsafe(TOKEN_BYTES), persona=persona)
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str | None) -> ChatSession | None:
        if not session_id:
            return None
        with self._lock:
            return self._sessions.get(session_id)

    def end(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)

    def spend_message(self, session: ChatSession) -> int:
        """Claim one of this session's messages, or refuse.

        The count is claimed before the work starts, not after it succeeds. A run
        that fails still cost tokens, and a public demo where failures are free is
        a public demo someone can drain by asking for failures.
        """
        with self._lock:
            if session.messages_used >= self._max_messages:
                raise SessionLimitReached(
                    f"this session has used its {self._max_messages} messages; "
                    "start a new one to continue"
                )
            session.messages_used += 1
            return self._max_messages - session.messages_used

    def remaining(self, session: ChatSession) -> int:
        with self._lock:
            return max(self._max_messages - session.messages_used, 0)

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)

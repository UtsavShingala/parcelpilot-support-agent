"""Everything the service needs, built once at startup.

The corpus, the index and the persona roster are read-only and identical for every
visitor, so they are built at startup rather than per request. The alternative --
opening SQLite and rebuilding the BM25 index on each question -- would add hundreds
of milliseconds to every turn for no benefit.

The action ledger is the exception: it is written to. It is opened per request
instead, because SQLite connections are not safe to share across threads and
FastAPI will happily run two confirmations at once.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from parcelpilot.agent.loop import SupportAgent
from parcelpilot.agent.provider import build_model_client, describe_mode, mode_of
from parcelpilot.agent.registry import build_registry
from parcelpilot.agent.tools.actions import ActionLedger
from parcelpilot.api.sessions import SessionStore
from parcelpilot.auth.personas import Persona, find_persona, open_personas
from parcelpilot.config import Settings, get_settings
from parcelpilot.data.queries import OperationalData
from parcelpilot.ingest.documents import Chunk
from parcelpilot.retrieval.store import DocumentStore


@dataclass
class Runtime:
    """Long-lived application state."""

    settings: Settings
    data: OperationalData
    agent: SupportAgent
    personas: list[Persona]
    sessions: SessionStore
    chunks: list[Chunk]
    mode: str
    mode_description: str

    @property
    def snapshot_at(self) -> datetime:
        return self.data.snapshot_at

    def persona(self, persona_id: str) -> Persona | None:
        return find_persona(self.personas, persona_id)

    @contextmanager
    def ledger(self) -> Iterator[ActionLedger]:
        """A ledger connection for one request.

        Opened per request rather than shared: SQLite connections belong to the
        thread that made them, and two visitors confirming at once would otherwise
        collide.
        """
        ledger = ActionLedger(self.settings.actions_path, effective_at=self.snapshot_at)
        try:
            yield ledger
        finally:
            ledger.close()

    def close(self) -> None:
        self.data.close()


def build_runtime(settings: Settings | None = None) -> Runtime:
    settings = settings or get_settings()
    data = OperationalData.open(settings)
    client = build_model_client(settings)
    store = DocumentStore.from_settings(settings)

    return Runtime(
        settings=settings,
        data=data,
        agent=SupportAgent(
            registry=build_registry(store, data),
            client=client,
            snapshot_at=data.snapshot_at,
            max_steps=settings.max_agent_steps,
            max_seconds=settings.max_turn_seconds,
        ),
        personas=open_personas(settings),
        sessions=SessionStore(max_messages=settings.max_messages_per_session),
        # Shared with the detectors, so the ops view and an answer read the
        # same documents rather than two separately-built copies.
        chunks=store.chunks,
        mode=mode_of(client),
        mode_description=describe_mode(settings),
    )


__all__ = ["Runtime", "build_runtime"]

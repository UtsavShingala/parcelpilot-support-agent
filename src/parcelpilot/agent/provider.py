"""Which model client backs a run.

One place decides, so the CLI and the HTTP service cannot drift into disagreeing
about what ``SCRIPTED=1`` means -- and so the answer to "what is actually running?"
has a single source rather than being reconstructed from two call sites.

The choice is explicit. Falling back to scripted mode whenever a key happens to be
missing would be friendlier and worse: a deployment with a typo in its key would
quietly serve assembled answers that look like reasoned ones, which is the one
failure this system should never have.
"""

from __future__ import annotations

from parcelpilot.agent.model import ModelClient, OpenAIModelClient
from parcelpilot.agent.scripted import ScriptedModelClient
from parcelpilot.config import Settings, get_settings

SCRIPTED_MODE = "scripted"
LIVE_MODE = "openai"


def build_model_client(settings: Settings | None = None) -> ModelClient:
    """The client this configuration asks for.

    Raises :class:`ModelUnavailable` when live mode is selected without a key, so
    the failure lands at startup rather than inside a visitor's first question.
    """
    settings = settings or get_settings()
    if settings.scripted:
        return ScriptedModelClient()
    return OpenAIModelClient(api_key=settings.openai_api_key, model=settings.openai_model)


def mode_of(client: ModelClient) -> str:
    """The label an interface should show for ``client``."""
    return getattr(client, "name", LIVE_MODE)


def describe_mode(settings: Settings | None = None) -> str:
    """A one-line account of what will answer, for logs and the session payload."""
    settings = settings or get_settings()
    if settings.scripted:
        return "scripted (deterministic; no model is called)"
    return f"{LIVE_MODE} ({settings.openai_model})"


__all__ = ["LIVE_MODE", "SCRIPTED_MODE", "build_model_client", "describe_mode", "mode_of"]

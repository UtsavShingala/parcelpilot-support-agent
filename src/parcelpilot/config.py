"""Runtime configuration.

Paths are resolved against the repository root rather than the current working
directory, so ingest and retrieval behave the same whether they are driven from
a shell, a test, or the API process.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    """Where relative paths are anchored.

    In a checkout this is the repository root, found by walking up to pyproject.toml.
    In a container the package is installed rather than laid out in src/, so no such
    marker exists above it -- counting parent directories would land somewhere inside
    site-packages and quietly look for the corpus there. Falling back to the working
    directory puts it where the image actually placed the data.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return Path.cwd()


REPO_ROOT = _project_root()

# Corpus name -> directory beneath ``data_dir``. Names not listed here resolve to a
# directory of the same name, so adding a corpus needs no code change.
_CORPUS_DIRECTORIES = {"parcelpilot": "raw"}


class Settings(BaseSettings):
    """Settings read from the environment, falling back to committed defaults."""

    # The env file is resolved against the repository root, not the working
    # directory. A relative path silently loads nothing when the process is started
    # from anywhere else -- which reads as "the key is wrong" rather than "the key
    # was never read".
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    corpus: str = "parcelpilot"
    data_dir: Path = Path("data")
    index_dir: Path = Path("data/index")

    # The model provider, named for the protocol rather than the vendor. Several
    # providers speak the OpenAI chat-completions API -- OpenAI, Gemini through its
    # compatibility endpoint, OpenRouter, Together -- so pointing at a different one
    # is a base URL and a model name, not a rewrite. The OPENAI_* spellings are
    # accepted as aliases so an existing .env keeps working.
    model_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "MODEL_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"
        ),
    )
    model_name: str = Field(
        default="gemini-3.7-flash",
        validation_alias=AliasChoices("MODEL_NAME", "OPENAI_MODEL", "GEMINI_MODEL"),
    )
    # Tried in order when the configured model is overloaded or out of quota. Both
    # are per-model conditions, so a second name is the cheapest available recovery
    # -- and over one afternoon three different Gemini models each went unavailable
    # at some point while the others answered fine.
    model_fallbacks: str = "gemini-3.6-flash,gemini-3.5-flash,gemini-3.7-flash"

    # How long one model request may take. An ops question can run six tool calls,
    # and each request carries the whole conversation so far -- the last one is the
    # slowest and the most expensive to lose. Sixty seconds cut those off after the
    # work was already done.
    model_timeout_seconds: float = 120.0

    model_base_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta/openai/",
        validation_alias=AliasChoices("MODEL_BASE_URL", "OPENAI_BASE_URL"),
    )

    # Run the deterministic client instead of calling a provider. A real mode, not a
    # test switch: it lets the whole pipeline be demonstrated and deployed with no
    # credentials, and keeps the demo alive when a budget is spent or a provider is
    # down. The interface labels it, so an assembled answer is never mistaken for a
    # reasoned one.
    scripted: bool = False

    # A question needing more than this many tool round-trips is not converging;
    # the agent escalates rather than spending more of someone else's money on it.
    max_agent_steps: int = 12
    max_messages_per_session: int = 25

    # Where the record of confirmed actions lives. Defaults beside the index but is
    # settable on its own, because it is the one file here that is not disposable:
    # a deployment will want it on a durable volume while index artifacts are rebuilt.
    actions_db: Path | None = None

    def _resolve(self, path: Path) -> Path:
        return path if path.is_absolute() else REPO_ROOT / path

    @property
    def corpus_dir(self) -> Path:
        """Directory holding the source documents for the selected corpus."""
        directory = _CORPUS_DIRECTORIES.get(self.corpus, self.corpus)
        return self._resolve(self.data_dir) / directory

    @property
    def index_path(self) -> Path:
        """Directory holding generated build artifacts."""
        return self._resolve(self.index_dir)

    @property
    def chunks_path(self) -> Path:
        return self.index_path / f"{self.corpus}_chunks.json"

    @property
    def database_path(self) -> Path:
        return self.index_path / f"{self.corpus}.db"

    @property
    def actions_path(self) -> Path:
        """Ledger of confirmed actions.

        Deliberately a separate file from the corpus database: rebuilding the corpus
        must not erase a record of something the system actually did.
        """
        if self.actions_db is not None:
            return self._resolve(self.actions_db)
        return self.index_path / f"{self.corpus}_actions.db"

    @property
    def fallback_models(self) -> list[str]:
        return [name.strip() for name in self.model_fallbacks.split(",") if name.strip()]

    @property
    def has_model_credentials(self) -> bool:
        return bool(self.model_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

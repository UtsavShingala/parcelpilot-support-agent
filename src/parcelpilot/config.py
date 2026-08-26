"""Runtime configuration.

Paths are resolved against the repository root rather than the current working
directory, so ingest and retrieval behave the same whether they are driven from
a shell, a test, or the API process.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

# Corpus name -> directory beneath ``data_dir``. Names not listed here resolve to a
# directory of the same name, so adding a corpus needs no code change.
_CORPUS_DIRECTORIES = {"parcelpilot": "raw"}


class Settings(BaseSettings):
    """Settings read from the environment, falling back to committed defaults."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    corpus: str = "parcelpilot"
    data_dir: Path = Path("data")
    index_dir: Path = Path("data/index")

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

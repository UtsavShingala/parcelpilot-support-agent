"""Serving the interface must not disturb serving the API.

These exist because the catch-all route is only registered when a built frontend is
present. Without a test that builds one, every API test ran against an app missing
the route entirely -- and the first real deployment failed at import time on a
response-model error no test could have seen.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parcelpilot.api.main import create_app, static_root
from parcelpilot.config import Settings


@pytest.fixture(scope="module")
def served(
    corpus_dir: Path, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[TestClient]:
    """A service with a built frontend beside it.

    A real bundle is not required: the route only cares that index.html and assets/
    exist, so a minimal stand-in exercises the same paths without coupling the
    Python tests to a node build.
    """
    root = tmp_path_factory.mktemp("static")
    (root / "assets").mkdir()
    (root / "index.html").write_text("<!doctype html><title>app shell</title>", "utf-8")
    (root / "assets" / "index.js").write_text("console.log('built');", "utf-8")

    settings = Settings(
        scripted=True, actions_db=tmp_path_factory.mktemp("ledger") / "actions.db"
    )
    with TestClient(create_app(settings, static_dir=root)) as running:
        yield running


def test_the_application_starts_with_a_catch_all_registered(served: TestClient) -> None:
    """The regression: a union return type made FastAPI refuse to build the app."""
    assert served.get("/api/health").json()["status"] == "ok"


def test_the_root_serves_the_app_shell(served: TestClient) -> None:
    response = served.get("/")
    assert response.status_code == 200
    assert "app shell" in response.text


def test_an_unknown_path_falls_back_to_the_shell(served: TestClient) -> None:
    """The interface routes client-side, so a reload on any path must not 404."""
    assert "app shell" in served.get("/some/deep/route").text


def test_a_real_asset_is_served_rather_than_the_shell(served: TestClient) -> None:
    assert "console.log" in served.get("/assets/index.js").text


def test_an_unknown_api_path_stays_a_json_404(served: TestClient) -> None:
    """Falling back to HTML here would turn a typo into a JSON parse error."""
    response = served.get("/api/nonexistent")
    assert response.status_code == 404
    assert response.json()["detail"] == "not found"


def test_api_routes_are_not_shadowed_by_the_catch_all(served: TestClient) -> None:
    response = served.get("/api/personas")
    assert response.status_code == 200
    assert response.json()["personas"]


def test_a_missing_bundle_is_not_an_error(corpus_dir: Path, tmp_path: Path) -> None:
    """The API must still serve when no frontend has been built."""
    settings = Settings(scripted=True, actions_db=tmp_path / "actions.db")
    with TestClient(create_app(settings)) as running:
        assert running.get("/api/health").status_code == 200


def test_static_root_reports_where_the_bundle_was_found() -> None:
    root = static_root()
    assert root is None or (root / "index.html").is_file()

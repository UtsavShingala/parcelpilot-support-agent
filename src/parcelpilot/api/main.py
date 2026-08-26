"""The service: one process, one origin, API and interface together.

The built frontend is served from the same origin as the API. That removes CORS
entirely, which is not laziness -- CORS on a credentialed API means preflight
rules and a cookie policy to get wrong, and the whole class of mistake disappears
if there is only one origin. It is also one deployment and one URL to hand a
reviewer.

Static files are mounted last, at the root, so ``/api/*`` is matched first and a
missing asset cannot shadow an endpoint. Unknown paths fall back to index.html
because the interface routes client-side; a reload on any path must not 404.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from parcelpilot.api.routes import actions, chat, insights, session
from parcelpilot.api.runtime import build_runtime
from parcelpilot.config import REPO_ROOT, Settings

# Where the Dockerfile puts the built frontend, then where a local `npm run build`
# leaves it. Neither existing is fine: the API still serves, and the interface is
# simply not there.
STATIC_CANDIDATES = (Path("static"), REPO_ROOT / "static", REPO_ROOT / "web" / "dist")


def static_root() -> Path | None:
    for candidate in STATIC_CANDIDATES:
        if (candidate / "index.html").is_file():
            return candidate
    return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.runtime = build_runtime(getattr(app.state, "settings", None))
    try:
        yield
    finally:
        app.state.runtime.close()


def create_app(
    settings: Settings | None = None, *, static_dir: Path | None = None
) -> FastAPI:
    """Build the application.

    ``static_dir`` overrides where the built interface is looked for. It defaults to
    discovery, which is what a deployment wants; passing it explicitly is how a test
    serves a known bundle instead of whatever this machine happens to have built.
    """
    app = FastAPI(
        title="ParcelPilot Support Agent",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings

    app.include_router(session.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(actions.router, prefix="/api")
    app.include_router(insights.router, prefix="/api")

    @app.get("/api/health")
    def health(request: Request) -> dict[str, object]:
        runtime = request.app.state.runtime
        return {
            "status": "ok",
            "mode": runtime.mode,
            "snapshot_at": runtime.snapshot_at.isoformat(),
            "sessions": len(runtime.sessions),
        }

    root = static_dir if static_dir is not None else static_root()
    if root is not None and (root / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

        # response_model=None: the return type is a union of responses, which FastAPI
        # would otherwise try to build a Pydantic response model from and reject.
        @app.get("/{path:path}", response_model=None)
        def spa(path: str) -> FileResponse | JSONResponse:
            """Serve a real file if it exists, otherwise the app shell.

            An unknown /api/* path must still look like a missing endpoint rather
            than silently returning HTML, which would turn a typo into a confusing
            JSON parse error in the client.
            """
            if path.startswith("api/"):
                return JSONResponse({"detail": "not found"}, status_code=404)
            candidate = root / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(root / "index.html")

    return app


app = create_app()

__all__ = ["app", "create_app", "static_root"]

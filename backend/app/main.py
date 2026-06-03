"""FastAPI application factory.

In production (Heroku) the same dyno serves the API at ``/api/*`` and the
pre-built Vite frontend from ``frontend/dist/``. In development the
frontend is run separately by ``vite dev`` on port 5173 and proxies
``/api`` calls back here.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.cache.redis_client import close_redis
from app.middleware.csrf import CsrfMiddleware
from app.middleware.rate_limit import limiter
from app.realtime import start_listener, stop_listener
from app.routers import (
    access_request, ai_verify, api_keys, auth, export, extraction,
    extraction_verify, health, history, hmo_studio, invites, onboarding,
    projects, rdf, runs, section_export, section_import,
    wikidata_labels, wikidata_studio, ws,
)
from app.settings import get_settings


@asynccontextmanager
async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
    """Boot/teardown — keep the Postgres LISTEN bridge running across
    the whole process lifetime."""
    await start_listener()
    try:
        yield
    finally:
        await stop_listener()
        await close_redis()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="MHM Pipeline Web",
        version="0.1.0",
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # CORS — needed during dev when the frontend runs on 5173. In
    # production the frontend is served from the same origin, so the
    # CORS layer is a no-op (no cross-origin requests).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        allow_headers=["*"],
    )

    app.add_middleware(CsrfMiddleware)

    # Routers — all under /api so the static-file mount below doesn't
    # shadow them.
    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(onboarding.router, prefix="/api")
    app.include_router(invites.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(runs.router, prefix="/api")
    app.include_router(extraction.router, prefix="/api")
    app.include_router(rdf.router, prefix="/api")
    app.include_router(history.router, prefix="/api")
    app.include_router(export.router, prefix="/api")
    app.include_router(api_keys.router, prefix="/api")
    app.include_router(wikidata_studio.router, prefix="/api")
    app.include_router(hmo_studio.router, prefix="/api")
    app.include_router(wikidata_labels.router, prefix="/api")
    app.include_router(ai_verify.router, prefix="/api")
    app.include_router(extraction_verify.router, prefix="/api")
    app.include_router(access_request.router, prefix="/api")
    app.include_router(section_export.router, prefix="/api")
    app.include_router(section_import.router, prefix="/api")
    app.include_router(ws.router, prefix="/api")

    # Frontend static assets (production). Mounted last so any /api
    # route still wins.
    _mount_frontend(app)

    return app


def _mount_frontend(app: FastAPI) -> None:
    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if not frontend_dist.exists():
        return  # dev mode: ``vite dev`` serves the assets directly

    assets_dir = frontend_dist / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    index_file = frontend_dist / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:  # noqa: ARG001
        """Catch-all so client-side React Router handles deep links."""
        return FileResponse(index_file)


app = create_app()

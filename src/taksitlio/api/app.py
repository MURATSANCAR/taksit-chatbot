"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from taksitlio.app.container import (
    AppContainer,
    build_in_memory_container,
    build_production_container,
)
from taksitlio.config.settings import InfraSettings
from taksitlio.api.routes import (
    admin,
    admin_finance,
    admin_answer_integrity,
    admin_golden,
    admin_ingestion,
    admin_media,
    answer_integrity,
    chat,
    health,
    product_query,
    search_sessions,
)

_WEB_TAKSITLIO = Path(__file__).resolve().parents[3] / "web" / "taksitlio"


def _maybe_mount_local_cdn(app: FastAPI, container: AppContainer | None) -> None:
    """Serve MEDIA_STORAGE_ROOT at /cdn when using local object storage."""

    import os

    backend = (os.environ.get("OBJECT_STORAGE_BACKEND") or "local").strip().lower()
    if backend not in {"", "local"}:
        return
    root = os.environ.get("MEDIA_STORAGE_ROOT")
    if not root:
        if container is not None:
            storage = container.extras.get("media_storage")
            root = getattr(storage, "root", None)
            root = str(root) if root is not None else None
    if not root:
        return
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    # Avoid remount if already present
    for route in app.routes:
        if getattr(route, "path", None) == "/cdn":
            return
    app.mount("/cdn", StaticFiles(directory=str(path)), name="media-cdn")


def create_app(container: AppContainer | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if container is not None:
            app.state.container = container
        else:
            settings = InfraSettings.from_env(allow_missing=True)
            if settings.allow_in_memory:
                app.state.container = build_in_memory_container(settings)
            else:
                app.state.container = await build_production_container(settings)
        _maybe_mount_local_cdn(app, app.state.container)
        yield
        await app.state.container.aclose()

    app = FastAPI(
        title="Taksitlio Chatbot API",
        version="0.2.0",
        description="Gerçek zamanlı Türkçe anlama + kampanya öneri MVP",
        lifespan=lifespan,
    )
    if container is not None:
        app.state.container = container
    app.include_router(health.router)
    app.include_router(chat.router, prefix="/v1")
    app.include_router(product_query.router, prefix="/v1")
    app.include_router(search_sessions.router, prefix="/v1")
    app.include_router(answer_integrity.router, prefix="/v1")
    app.include_router(admin.router, prefix="/v1/admin")
    app.include_router(admin_ingestion.router, prefix="/v1/admin")
    app.include_router(admin_answer_integrity.router, prefix="/v1/admin")
    app.include_router(admin_finance.router, prefix="/v1/admin")
    app.include_router(admin_golden.router, prefix="/v1/admin")
    app.include_router(admin_media.router, prefix="/v1/admin")
    if _WEB_TAKSITLIO.is_dir():
        # Guest chatbot UI — wired to POST /v1/chat progressive cards (P14)
        app.mount(
            "/taksitlio",
            StaticFiles(directory=str(_WEB_TAKSITLIO), html=True),
            name="taksitlio-portal",
        )
    if container is not None:
        _maybe_mount_local_cdn(app, container)
    return app


def get_container(app: FastAPI) -> AppContainer:
    return app.state.container  # type: ignore[no-any-return]

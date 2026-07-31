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
from taksitlio.api.routes import admin, admin_ingestion, chat, health, product_query

_WEB_TAKSITLIO = Path(__file__).resolve().parents[3] / "web" / "taksitlio"


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
    app.include_router(admin.router, prefix="/v1/admin")
    app.include_router(admin_ingestion.router, prefix="/v1/admin")
    if _WEB_TAKSITLIO.is_dir():
        # Guest chatbot UI — later wired to /v1/chat (portal.nanobase.ai/taksitlio)
        app.mount(
            "/taksitlio",
            StaticFiles(directory=str(_WEB_TAKSITLIO), html=True),
            name="taksitlio-portal",
        )
    return app


def get_container(app: FastAPI) -> AppContainer:
    return app.state.container  # type: ignore[no-any-return]

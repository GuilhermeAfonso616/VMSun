"""Fabrica da aplicacao FastAPI e composicao das rotas."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import bootstrap
from app.api.routes import router as api_router
from app.core.config import settings
from app.core.logging import RequestLoggingMiddleware, get_logger
from app.internal.routes import router as internal_router
from app.web.web_routes import router as web_router


def resolve_static_dir() -> Path | None:
    candidates = [
        Path.cwd() / "app" / "static",
        Path(__file__).resolve().parent / "static",
    ]
    return next((candidate for candidate in candidates if candidate.exists()), None)


from app.services.event_broadcaster import event_broadcaster


@asynccontextmanager
async def _application_lifespan(_application: FastAPI):
    try:
        loop = asyncio.get_running_loop()
        event_broadcaster.set_loop(loop)
    except Exception:
        pass
    bootstrap.startup_application()
    try:
        yield
    finally:
        bootstrap.shutdown_application()


def create_app(*, enable_lifecycle: bool = True) -> FastAPI:
    """Constroi a aplicacao sem iniciar banco, threads ou servicos imediatamente."""
    if os.getenv("SERVER_ANALITICO_WORKER_CHILD") == "1":
        return FastAPI(title=settings.app_name)

    application = FastAPI(
        title=settings.app_name,
        lifespan=_application_lifespan if enable_lifecycle else None,
    )
    application.add_middleware(RequestLoggingMiddleware)

    static_dir = resolve_static_dir()
    if static_dir is not None:
        application.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    if bootstrap.runtime_role_enabled() or bootstrap.web_role_enabled():
        application.include_router(api_router)
    if bootstrap.runtime_role_enabled():
        application.include_router(internal_router)
    if bootstrap.web_role_enabled():
        application.include_router(web_router)

    get_logger("app.startup").debug("Aplicacao FastAPI construida sem iniciar recursos de processo")
    return application

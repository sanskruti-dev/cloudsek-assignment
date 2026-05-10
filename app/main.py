"""FastAPI app factory and entry point.

Run with: ``uvicorn app.main:app --host 0.0.0.0 --port 8000``.

The lifespan handler owns Mongo client setup/teardown, fetcher cleanup, and
draining of in-flight background tasks before the process exits.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import __version__
from app.api.routes import health as health_routes
from app.api.routes import metadata as metadata_routes
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.mongo import build_client, ensure_indexes, wait_for_mongo
from app.db.repository import MetadataRepository
from app.services.fetcher import Fetcher
from app.services.metadata_service import MetadataService
from app.services.worker import BackgroundTaskScheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings or get_settings()
    app.state.settings = settings

    configure_logging(settings.log_level)
    logger.info(
        "app.startup",
        extra={
            "app_env": settings.app_env,
            "version": __version__,
            "mongo_db": settings.mongo_db,
        },
    )

    client = build_client(settings)
    await wait_for_mongo(
        client,
        attempts=settings.mongo_startup_retry_attempts,
        delay_s=settings.mongo_startup_retry_delay_s,
    )
    database = client[settings.mongo_db]
    await ensure_indexes(database, settings.mongo_collection)

    repository = MetadataRepository(database, settings.mongo_collection)
    fetcher = Fetcher(settings)
    scheduler = BackgroundTaskScheduler()
    service = MetadataService(
        settings=settings,
        repository=repository,
        fetcher=fetcher,
        schedule=lambda factory, name: scheduler.schedule(factory, name),
    )

    app.state.mongo_client = client
    app.state.database = database
    app.state.repository = repository
    app.state.fetcher = fetcher
    app.state.scheduler = scheduler
    app.state.service = service

    try:
        yield
    finally:
        logger.info("app.shutdown.start", extra={"pending_tasks": scheduler.pending})
        await scheduler.drain(timeout=30.0)
        await fetcher.aclose()
        client.close()
        logger.info("app.shutdown.done")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Pass ``settings`` explicitly in tests to control configuration."""
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)

    app = FastAPI(
        title="HTTP Metadata Inventory",
        version=__version__,
        description=(
            "Collects, stores, and serves HTTP response metadata "
            "(headers, cookies, page source) for arbitrary URLs."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.state.settings = resolved

    app.include_router(metadata_routes.router, prefix=resolved.api_prefix)
    app.include_router(health_routes.router)

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Don't echo raw input back; just enumerate the field paths and codes.
        cleaned = [
            {"loc": err.get("loc"), "msg": err.get("msg"), "type": err.get("type")}
            for err in exc.errors()
        ]
        logger.info(
            "request.validation_error",
            extra={"path": str(request.url.path), "errors": cleaned},
        )
        return JSONResponse(status_code=422, content={"detail": cleaned})

    return app


app = create_app()

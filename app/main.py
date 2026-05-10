"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

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
    settings: Settings = getattr(app.state, "settings", None) or get_settings()
    app.state.settings = settings

    configure_logging(settings.log_level)
    logger.info("starting up (mongo_db=%s)", settings.mongo_db)

    client = build_client(settings.mongo_uri)
    await wait_for_mongo(client)
    database = client[settings.mongo_db]
    await ensure_indexes(database, settings.mongo_collection)

    repository = MetadataRepository(database, settings.mongo_collection)
    fetcher = Fetcher()
    scheduler = BackgroundTaskScheduler()
    service = MetadataService(
        repository=repository,
        fetcher=fetcher,
        schedule=lambda factory, name: scheduler.schedule(factory, name),
    )

    app.state.mongo_client = client
    app.state.repository = repository
    app.state.fetcher = fetcher
    app.state.scheduler = scheduler
    app.state.service = service

    try:
        yield
    finally:
        logger.info("shutting down (%d background task(s) pending)", scheduler.pending)
        await scheduler.drain(timeout=30.0)
        await fetcher.aclose()
        client.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)

    app = FastAPI(
        title="HTTP Metadata Inventory",
        description="Collects and serves HTTP metadata (headers, cookies, page source).",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.include_router(metadata_routes.router)

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        cleaned = [
            {"loc": err.get("loc"), "msg": err.get("msg"), "type": err.get("type")}
            for err in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": cleaned})

    return app


app = create_app()

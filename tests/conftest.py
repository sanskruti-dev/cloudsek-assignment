"""Shared pytest fixtures.

The suite is hermetic: ``mongomock-motor`` replaces MongoDB and ``respx``
replaces httpx, so no external services are needed to run the tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from app.api.routes import health as health_routes
from app.api.routes import metadata as metadata_routes
from app.core.config import Settings
from app.db.repository import MetadataRepository
from app.services.fetcher import Fetcher
from app.services.metadata_service import MetadataService
from app.services.worker import BackgroundTaskScheduler


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env="test",
        log_level="WARNING",
        mongo_uri="mongodb://localhost:27017",
        mongo_db="metadata_inventory_test",
        mongo_collection="url_metadata_test",
        mongo_startup_retry_attempts=1,
        mongo_startup_retry_delay_s=0.0,
        fetch_timeout_s=2.0,
        fetch_max_redirects=2,
        fetch_max_bytes=64 * 1024,
        fetch_user_agent="test-agent/1.0",
        block_private_networks=False,
        ALLOWED_SCHEMES="http,https",
    )


@pytest_asyncio.fixture
async def mongo_client() -> AsyncIterator[AsyncMongoMockClient]:
    client = AsyncMongoMockClient()
    try:
        yield client
    finally:
        client.close()


@pytest_asyncio.fixture
async def repository(
    settings: Settings, mongo_client: AsyncMongoMockClient
) -> MetadataRepository:
    db = mongo_client[settings.mongo_db]
    return MetadataRepository(db, settings.mongo_collection)


@pytest.fixture
def respx_mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router


@pytest_asyncio.fixture
async def httpx_client(respx_mock: respx.MockRouter) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.MockTransport(respx_mock.handler)
    client = httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(2.0),
        follow_redirects=True,
        max_redirects=3,
        headers={"User-Agent": "test-agent/1.0"},
    )
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def fetcher(
    settings: Settings, httpx_client: httpx.AsyncClient
) -> AsyncIterator[Fetcher]:
    f = Fetcher(settings, client=httpx_client)
    try:
        yield f
    finally:
        await f.aclose()


@pytest.fixture
def scheduler() -> BackgroundTaskScheduler:
    return BackgroundTaskScheduler()


@pytest_asyncio.fixture
async def service(
    settings: Settings,
    repository: MetadataRepository,
    fetcher: Fetcher,
    scheduler: BackgroundTaskScheduler,
) -> MetadataService:
    return MetadataService(
        settings=settings,
        repository=repository,
        fetcher=fetcher,
        schedule=lambda factory, name: scheduler.schedule(factory, name),
    )


@pytest.fixture
def app(
    settings: Settings,
    repository: MetadataRepository,
    fetcher: Fetcher,
    scheduler: BackgroundTaskScheduler,
    service: MetadataService,
    mongo_client: AsyncMongoMockClient,
) -> FastAPI:
    """Build a FastAPI app with state pre-wired so we skip the real lifespan."""
    test_app = FastAPI(title="HTTP Metadata Inventory (test)")
    test_app.include_router(metadata_routes.router, prefix=settings.api_prefix)
    test_app.include_router(health_routes.router)

    test_app.state.settings = settings
    test_app.state.mongo_client = mongo_client
    test_app.state.database = mongo_client[settings.mongo_db]
    test_app.state.repository = repository
    test_app.state.fetcher = fetcher
    test_app.state.scheduler = scheduler
    test_app.state.service = service
    return test_app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def example_html() -> str:
    return (
        "<!doctype html><html><head><title>Example</title></head>"
        "<body><h1>Hello</h1></body></html>"
    )


@pytest.fixture
def expected_response_headers() -> dict[str, Any]:
    return {
        "Content-Type": "text/html; charset=utf-8",
        "X-Custom": "value",
        "Set-Cookie": "session=abc123; Path=/; HttpOnly; Secure",
    }

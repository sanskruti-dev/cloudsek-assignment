"""Shared pytest fixtures.

The suite uses ``mongomock-motor`` and ``respx`` so no external services
need to be running.
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

from app.api.routes import metadata as metadata_routes
from app.core.config import Settings
from app.db.repository import MetadataRepository
from app.services.fetcher import Fetcher
from app.services.metadata_service import MetadataService
from app.services.worker import BackgroundTaskScheduler


@pytest.fixture
def settings() -> Settings:
    return Settings(
        mongo_uri="mongodb://localhost:27017",
        mongo_db="metadata_inventory_test",
        mongo_collection="url_metadata_test",
        log_level="WARNING",
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
    )
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def fetcher(httpx_client: httpx.AsyncClient) -> AsyncIterator[Fetcher]:
    f = Fetcher(client=httpx_client)
    try:
        yield f
    finally:
        await f.aclose()


@pytest.fixture
def scheduler() -> BackgroundTaskScheduler:
    return BackgroundTaskScheduler()


@pytest_asyncio.fixture
async def service(
    repository: MetadataRepository,
    fetcher: Fetcher,
    scheduler: BackgroundTaskScheduler,
) -> MetadataService:
    return MetadataService(
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
    test_app = FastAPI()
    test_app.include_router(metadata_routes.router)

    test_app.state.settings = settings
    test_app.state.mongo_client = mongo_client
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

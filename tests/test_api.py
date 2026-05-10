"""End-to-end API tests using FastAPI's TestClient."""

from __future__ import annotations

import asyncio

import httpx
import respx
from fastapi.testclient import TestClient

from app.services.worker import BackgroundTaskScheduler


def test_post_creates_complete_record(
    client: TestClient,
    respx_mock: respx.MockRouter,
    example_html: str,
) -> None:
    respx_mock.get("https://example.com/").mock(
        return_value=httpx.Response(
            200,
            content=example_html.encode(),
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
    )

    response = client.post("/metadata", json={"url": "https://example.com/"})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "complete"
    assert body["status_code"] == 200
    assert body["normalized_url"] == "https://example.com/"
    assert body["page_source"] == example_html


def test_post_returns_502_on_fetch_failure(
    client: TestClient,
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get("https://broken.example/").mock(
        side_effect=httpx.ConnectError("dns")
    )
    response = client.post("/metadata", json={"url": "https://broken.example/"})
    assert response.status_code == 502
    body = response.json()
    assert "kind" in body["detail"]


def test_post_rejects_invalid_url(client: TestClient) -> None:
    response = client.post("/metadata", json={"url": "not-a-url"})
    assert response.status_code == 422


def test_post_rejects_extra_fields(client: TestClient) -> None:
    response = client.post(
        "/metadata", json={"url": "https://example.com/", "evil": "yes"}
    )
    assert response.status_code == 422


def test_get_cache_hit_returns_200(
    client: TestClient,
    respx_mock: respx.MockRouter,
    example_html: str,
) -> None:
    respx_mock.get("https://example.com/cache").mock(
        return_value=httpx.Response(200, content=example_html.encode())
    )
    primed = client.post("/metadata", json={"url": "https://example.com/cache"})
    assert primed.status_code == 201

    response = client.get("/metadata", params={"url": "https://example.com/cache"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "complete"
    assert body["page_source"] == example_html


def test_get_cache_miss_returns_202_and_schedules_worker(
    client: TestClient,
    respx_mock: respx.MockRouter,
    app,
    example_html: str,
) -> None:
    respx_mock.get("https://miss.example/").mock(
        return_value=httpx.Response(200, content=example_html.encode())
    )

    response = client.get("/metadata", params={"url": "https://miss.example/"})
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert body["normalized_url"] == "https://miss.example/"

    scheduler: BackgroundTaskScheduler = app.state.scheduler
    asyncio.get_event_loop().run_until_complete(scheduler.drain(timeout=5.0))

    response = client.get("/metadata", params={"url": "https://miss.example/"})
    assert response.status_code == 200
    assert response.json()["status"] == "complete"


def test_get_invalid_url_returns_400(client: TestClient) -> None:
    response = client.get("/metadata", params={"url": "not-a-url"})
    assert response.status_code == 400


def test_get_unsupported_scheme_returns_400(client: TestClient) -> None:
    response = client.get("/metadata", params={"url": "ftp://example.com/"})
    assert response.status_code == 400


def test_openapi_schema_present(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "/metadata" in schema["paths"]

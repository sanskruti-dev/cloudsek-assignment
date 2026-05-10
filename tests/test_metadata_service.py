"""Tests for the MetadataService orchestrator."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from app.models.schemas import MetadataStatus
from app.services.fetcher import FetchFailure
from app.services.metadata_service import MetadataService
from app.services.worker import BackgroundTaskScheduler
from app.utils.url import InvalidURLError


@pytest.mark.asyncio
async def test_collect_now_stores_complete_record(
    service: MetadataService,
    respx_mock: respx.MockRouter,
    example_html: str,
) -> None:
    respx_mock.get("https://example.com/").mock(
        return_value=httpx.Response(
            200,
            content=example_html.encode(),
            headers={"Content-Type": "text/html"},
        )
    )

    record = await service.collect_now("https://example.com/")
    assert record.status == MetadataStatus.COMPLETE
    assert record.status_code == 200
    assert record.normalized_url == "https://example.com/"
    assert record.page_source == example_html


@pytest.mark.asyncio
async def test_collect_now_persists_failure_and_reraises(
    service: MetadataService,
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get("https://broken.example/").mock(
        side_effect=httpx.ConnectError("dns")
    )
    with pytest.raises(FetchFailure):
        await service.collect_now("https://broken.example/")

    record, served = await service.get_or_schedule("https://broken.example/")
    assert served is True
    assert record.status == MetadataStatus.FAILED
    assert record.error is not None


@pytest.mark.asyncio
async def test_get_returns_complete_record_from_cache(
    service: MetadataService,
    respx_mock: respx.MockRouter,
    example_html: str,
    scheduler: BackgroundTaskScheduler,
) -> None:
    respx_mock.get("https://example.com/").mock(
        return_value=httpx.Response(200, content=example_html.encode())
    )

    await service.collect_now("https://example.com/")

    record, served = await service.get_or_schedule("https://example.com/")
    assert served is True
    assert record.status == MetadataStatus.COMPLETE
    assert scheduler.pending == 0


@pytest.mark.asyncio
async def test_get_miss_schedules_background_and_returns_pending(
    service: MetadataService,
    respx_mock: respx.MockRouter,
    scheduler: BackgroundTaskScheduler,
    example_html: str,
) -> None:
    respx_mock.get("https://fresh.example/").mock(
        return_value=httpx.Response(200, content=example_html.encode())
    )

    record, served = await service.get_or_schedule("https://fresh.example/")
    assert served is False
    assert record.status == MetadataStatus.PENDING

    await scheduler.drain(timeout=5.0)

    record, served = await service.get_or_schedule("https://fresh.example/")
    assert served is True
    assert record.status == MetadataStatus.COMPLETE
    assert record.page_source == example_html


@pytest.mark.asyncio
async def test_get_miss_schedules_only_one_worker_for_concurrent_requests(
    service: MetadataService,
    respx_mock: respx.MockRouter,
    scheduler: BackgroundTaskScheduler,
) -> None:
    route = respx_mock.get("https://race.example/").mock(
        return_value=httpx.Response(200, content=b"ok")
    )

    results = await asyncio.gather(
        *(service.get_or_schedule("https://race.example/") for _ in range(8))
    )
    served_flags = [served for _, served in results]
    assert all(flag is False for flag in served_flags)

    await scheduler.drain(timeout=5.0)
    # Exactly one worker -> exactly one upstream call, regardless of how many
    # GETs landed simultaneously.
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_invalid_url_raises(service: MetadataService) -> None:
    with pytest.raises(InvalidURLError):
        await service.collect_now("not-a-url")
    with pytest.raises(InvalidURLError):
        await service.get_or_schedule("ftp://example.com/")

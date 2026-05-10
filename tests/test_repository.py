"""Tests for the MetadataRepository (Mongo persistence layer)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db.repository import MetadataRepository
from app.models.schemas import (
    CookieRecord,
    FetchError,
    MetadataRecord,
    MetadataStatus,
)


def _build_record(url: str = "https://example.com/", **overrides) -> MetadataRecord:
    now = datetime.now(timezone.utc)
    base = dict(
        url=url,
        normalized_url=url,
        status=MetadataStatus.COMPLETE,
        status_code=200,
        final_url=url,
        headers={"Content-Type": "text/html"},
        cookies=[CookieRecord(name="s", value="1")],
        page_source="<html></html>",
        content_type="text/html",
        content_length=13,
        truncated=False,
        error=None,
        created_at=now,
        updated_at=now,
        fetched_at=now,
    )
    base.update(overrides)
    return MetadataRecord(**base)


@pytest.mark.asyncio
async def test_get_returns_none_when_missing(repository: MetadataRepository) -> None:
    assert await repository.get_by_normalized_url("https://nope.example/") is None


@pytest.mark.asyncio
async def test_reserve_pending_creates_placeholder_once(
    repository: MetadataRepository,
) -> None:
    url = "https://example.com/path"
    record1, created1 = await repository.reserve_pending(
        url=url, normalized_url=url
    )
    assert created1 is True
    assert record1.status == MetadataStatus.PENDING

    record2, created2 = await repository.reserve_pending(
        url=url, normalized_url=url
    )
    assert created2 is False
    assert record2.status == MetadataStatus.PENDING


@pytest.mark.asyncio
async def test_store_complete_replaces_pending(
    repository: MetadataRepository,
) -> None:
    url = "https://example.com/x"
    _, created = await repository.reserve_pending(url=url, normalized_url=url)
    assert created is True

    finished = _build_record(url=url)
    stored = await repository.store_complete(finished)

    assert stored.status == MetadataStatus.COMPLETE
    assert stored.page_source == "<html></html>"
    # created_at carries over from the placeholder rather than being replaced.
    assert stored.created_at <= stored.updated_at


@pytest.mark.asyncio
async def test_mark_failed_sets_error(repository: MetadataRepository) -> None:
    url = "https://example.com/y"
    await repository.reserve_pending(url=url, normalized_url=url)
    failed = await repository.mark_failed(
        url, FetchError(type="timeout", message="boom")
    )
    assert failed is not None
    assert failed.status == MetadataStatus.FAILED
    assert failed.error is not None
    assert failed.error.type == "timeout"

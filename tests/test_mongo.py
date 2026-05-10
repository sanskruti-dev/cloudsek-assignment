"""Tests for the Mongo connection retry helper."""

from __future__ import annotations

from typing import Any

import pytest
from pymongo.errors import ServerSelectionTimeoutError

from app.db.mongo import ensure_indexes, wait_for_mongo


class _FakeAdmin:
    def __init__(self, fail_first: int) -> None:
        self.fail_first = fail_first
        self.calls = 0

    async def command(self, command: str) -> dict[str, Any]:
        self.calls += 1
        if self.calls <= self.fail_first:
            raise ServerSelectionTimeoutError("not yet")
        return {"ok": 1}


class _FakeMotorClient:
    def __init__(self, fail_first: int = 0) -> None:
        self.admin = _FakeAdmin(fail_first)


@pytest.mark.asyncio
async def test_wait_for_mongo_succeeds_first_try() -> None:
    client = _FakeMotorClient()
    await wait_for_mongo(client, attempts=3, delay_s=0.0)  # type: ignore[arg-type]
    assert client.admin.calls == 1


@pytest.mark.asyncio
async def test_wait_for_mongo_retries_then_succeeds() -> None:
    client = _FakeMotorClient(fail_first=2)
    await wait_for_mongo(client, attempts=5, delay_s=0.0)  # type: ignore[arg-type]
    assert client.admin.calls == 3


@pytest.mark.asyncio
async def test_wait_for_mongo_gives_up_after_attempts() -> None:
    client = _FakeMotorClient(fail_first=10)
    with pytest.raises(RuntimeError):
        await wait_for_mongo(client, attempts=2, delay_s=0.0)  # type: ignore[arg-type]
    assert client.admin.calls == 2


@pytest.mark.asyncio
async def test_wait_for_mongo_rejects_zero_attempts() -> None:
    with pytest.raises(ValueError):
        await wait_for_mongo(_FakeMotorClient(), attempts=0, delay_s=0.0)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_ensure_indexes_creates_expected_indexes(
    mongo_client, settings
) -> None:
    db = mongo_client[settings.mongo_db]
    await ensure_indexes(db, settings.mongo_collection)
    info = await db[settings.mongo_collection].index_information()
    assert "uniq_normalized_url" in info
    assert "status_updated_at" in info

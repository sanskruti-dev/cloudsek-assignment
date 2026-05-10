"""Mongo client setup with startup retries."""

from __future__ import annotations

import asyncio
import logging

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import PyMongoError

STARTUP_RETRY_ATTEMPTS = 30
STARTUP_RETRY_DELAY_SECONDS = 2.0

logger = logging.getLogger(__name__)


def build_client(mongo_uri: str) -> AsyncIOMotorClient:
    return AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=5000)


async def wait_for_mongo(
    client: AsyncIOMotorClient,
    *,
    attempts: int = STARTUP_RETRY_ATTEMPTS,
    delay_s: float = STARTUP_RETRY_DELAY_SECONDS,
) -> None:
    """Block until ``ping`` succeeds or attempts are exhausted."""
    if attempts <= 0:
        raise ValueError("attempts must be >= 1")

    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            await client.admin.command("ping")
            logger.info("mongo ping ok (attempt %d)", attempt)
            return
        except PyMongoError as exc:
            last_exc = exc
            logger.warning("mongo ping failed (attempt %d/%d): %s", attempt, attempts, exc)
            await asyncio.sleep(delay_s)

    raise RuntimeError(f"Could not reach MongoDB after {attempts} attempts: {last_exc}")


async def ensure_indexes(database: AsyncIOMotorDatabase, collection_name: str) -> None:
    collection = database[collection_name]
    await collection.create_index(
        "normalized_url",
        unique=True,
        name="uniq_normalized_url",
    )
    await collection.create_index(
        [("status", 1), ("updated_at", 1)],
        name="status_updated_at",
    )

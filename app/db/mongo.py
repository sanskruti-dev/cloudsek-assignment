"""Async Mongo client setup with startup retries.

The retry loop matters because the API container can come up before mongod
is ready to accept connections, even with `depends_on: service_healthy`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import PyMongoError

from app.core.config import Settings

logger = logging.getLogger(__name__)


class MongoClientProtocol(Protocol):
    def __getitem__(self, name: str) -> AsyncIOMotorDatabase: ...

    async def admin_command(self, command: str) -> dict: ...

    def close(self) -> None: ...


def build_client(settings: Settings) -> AsyncIOMotorClient:
    return AsyncIOMotorClient(
        settings.mongo_uri,
        serverSelectionTimeoutMS=settings.mongo_server_selection_timeout_ms,
        connectTimeoutMS=settings.mongo_connect_timeout_ms,
        uuidRepresentation="standard",
        appname=settings.app_name,
    )


async def wait_for_mongo(
    client: AsyncIOMotorClient,
    *,
    attempts: int,
    delay_s: float,
) -> None:
    """Block until ``ping`` succeeds or ``attempts`` is exhausted."""
    if attempts <= 0:
        raise ValueError("attempts must be >= 1")

    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            await client.admin.command("ping")
            logger.info(
                "mongo.ping.ok",
                extra={"attempt": attempt, "attempts": attempts},
            )
            return
        except PyMongoError as exc:
            last_exc = exc
            logger.warning(
                "mongo.ping.retry",
                extra={
                    "attempt": attempt,
                    "attempts": attempts,
                    "error": exc.__class__.__name__,
                },
            )
            await asyncio.sleep(delay_s)

    assert last_exc is not None
    raise RuntimeError(
        f"Could not reach MongoDB after {attempts} attempts: {last_exc}"
    ) from last_exc


async def ensure_indexes(database: AsyncIOMotorDatabase, collection_name: str) -> None:
    collection = database[collection_name]
    await collection.create_index(
        "normalized_url",
        unique=True,
        name="uniq_normalized_url",
    )
    # Used by housekeeping queries (e.g. listing failed records by age).
    await collection.create_index(
        [("status", 1), ("updated_at", 1)],
        name="status_updated_at",
    )

"""Repository over the URL metadata collection.

Mongo specifics live here; the rest of the app talks ``MetadataRecord``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase

from app.models.schemas import (
    FetchError,
    MetadataRecord,
    MetadataStatus,
    fetched_record_to_dict,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _document_to_record(doc: dict[str, Any]) -> MetadataRecord:
    payload = {k: v for k, v in doc.items() if k != "_id"}
    return MetadataRecord.model_validate(payload)


class MetadataRepository:
    def __init__(self, database: AsyncIOMotorDatabase, collection_name: str) -> None:
        self._collection: AsyncIOMotorCollection = database[collection_name]

    @property
    def collection(self) -> AsyncIOMotorCollection:
        return self._collection

    async def get_by_normalized_url(
        self, normalized_url: str
    ) -> MetadataRecord | None:
        doc = await self._collection.find_one({"normalized_url": normalized_url})
        return _document_to_record(doc) if doc else None

    async def reserve_pending(
        self, *, url: str, normalized_url: str
    ) -> tuple[MetadataRecord, bool]:
        """Atomically ensure a record exists. Returns (record, just_created).

        ``just_created`` lets the caller decide whether to schedule a worker:
        only the inserting caller should, otherwise concurrent GETs on the
        same missing URL would each spawn a duplicate fetch.
        """
        now = _utcnow()
        result = await self._collection.update_one(
            {"normalized_url": normalized_url},
            {
                "$setOnInsert": {
                    "url": url,
                    "normalized_url": normalized_url,
                    "status": MetadataStatus.PENDING.value,
                    "headers": {},
                    "cookies": [],
                    "page_source": None,
                    "status_code": None,
                    "final_url": None,
                    "content_type": None,
                    "content_length": None,
                    "truncated": False,
                    "error": None,
                    "created_at": now,
                    "updated_at": now,
                    "fetched_at": None,
                }
            },
            upsert=True,
        )
        just_created = result.upserted_id is not None
        record = await self.get_by_normalized_url(normalized_url)
        if record is None:  # pragma: no cover - upsert just succeeded
            raise RuntimeError("Reserved record vanished between upsert and fetch")
        return record, just_created

    async def store_complete(self, record: MetadataRecord) -> MetadataRecord:
        """Replace the document for this URL with a fully fetched record."""
        now = _utcnow()
        # Preserve the original created_at if a placeholder already exists.
        existing = await self._collection.find_one(
            {"normalized_url": record.normalized_url},
            {"created_at": 1},
        )
        created_at = (existing or {}).get("created_at") or record.created_at or now

        document = fetched_record_to_dict(record)
        document["created_at"] = created_at
        document["updated_at"] = now

        await self._collection.replace_one(
            {"normalized_url": record.normalized_url},
            document,
            upsert=True,
        )
        stored = await self.get_by_normalized_url(record.normalized_url)
        assert stored is not None
        return stored

    async def mark_failed(
        self, normalized_url: str, error: FetchError
    ) -> MetadataRecord | None:
        now = _utcnow()
        await self._collection.update_one(
            {"normalized_url": normalized_url},
            {
                "$set": {
                    "status": MetadataStatus.FAILED.value,
                    "error": error.model_dump(mode="python"),
                    "updated_at": now,
                    "fetched_at": now,
                }
            },
        )
        return await self.get_by_normalized_url(normalized_url)

"""Application service. Orchestrates fetcher and repository.

POST     -> synchronous fetch + persist + return
GET hit  -> return as-is
GET miss -> reserve a placeholder atomically, schedule async work, return 202
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from app.db.repository import MetadataRepository
from app.models.schemas import (
    FetchError,
    MetadataRecord,
    MetadataStatus,
)
from app.services.fetcher import FetchFailure, FetchResult, Fetcher
from app.utils.url import InvalidURLError, ParsedURL, normalize_url

logger = logging.getLogger(__name__)

ScheduleCallable = Callable[[Callable[[], Awaitable[None]], str], None]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _build_record(
    parsed: ParsedURL,
    result: FetchResult,
    *,
    created_at: datetime | None = None,
) -> MetadataRecord:
    now = _utcnow()
    return MetadataRecord(
        url=parsed.original,
        normalized_url=parsed.normalized,
        status=MetadataStatus.COMPLETE,
        status_code=result.status_code,
        final_url=result.final_url,
        headers=result.headers,
        cookies=result.cookies,
        page_source=result.page_source,
        content_type=result.content_type,
        content_length=result.content_length,
        truncated=result.truncated,
        error=None,
        created_at=created_at or now,
        updated_at=now,
        fetched_at=result.fetched_at,
    )


class MetadataService:
    """Coordinates fetching and persistence."""

    def __init__(
        self,
        *,
        repository: MetadataRepository,
        fetcher: Fetcher,
        schedule: ScheduleCallable,
    ) -> None:
        self._repo = repository
        self._fetcher = fetcher
        self._schedule = schedule

    async def collect_now(self, raw_url: str) -> MetadataRecord:
        parsed = self._parse(raw_url)
        try:
            result = await self._fetcher.fetch(parsed)
        except FetchFailure as exc:
            await self._record_failure(parsed, exc)
            raise

        record = _build_record(parsed, result)
        stored = await self._repo.store_complete(record)
        logger.info("stored metadata for %s (%d)", parsed.normalized, result.status_code)
        return stored

    async def get_or_schedule(self, raw_url: str) -> tuple[MetadataRecord, bool]:
        """Return ``(record, served_from_cache)``.

        ``served_from_cache`` is True when the record is final (COMPLETE or
        FAILED). If False, the record is a pending placeholder and a worker
        is running in the background.
        """
        parsed = self._parse(raw_url)

        existing = await self._repo.get_by_normalized_url(parsed.normalized)
        if existing is not None and existing.status == MetadataStatus.COMPLETE:
            return existing, True

        record, just_created = await self._repo.reserve_pending(
            url=parsed.original, normalized_url=parsed.normalized
        )

        if just_created or record.status == MetadataStatus.PENDING:
            if just_created:
                self._schedule(
                    lambda: self._collect_in_background(parsed),
                    f"fetch:{parsed.normalized}",
                )
            return record, False

        return record, True

    def _parse(self, raw_url: str) -> ParsedURL:
        try:
            return normalize_url(raw_url)
        except InvalidURLError:
            raise
        except Exception as exc:
            raise InvalidURLError(f"Could not parse URL: {exc}") from exc

    async def _collect_in_background(self, parsed: ParsedURL) -> None:
        logger.info("background fetch start: %s", parsed.normalized)
        try:
            result = await self._fetcher.fetch(parsed)
        except FetchFailure as exc:
            await self._record_failure(parsed, exc)
            return
        except Exception as exc:
            logger.exception("unexpected error fetching %s", parsed.normalized)
            await self._record_failure(
                parsed,
                FetchFailure("unexpected", str(exc) or repr(exc)),
            )
            return

        existing = await self._repo.get_by_normalized_url(parsed.normalized)
        record = _build_record(
            parsed,
            result,
            created_at=existing.created_at if existing else None,
        )
        await self._repo.store_complete(record)
        logger.info("background fetch done: %s (%d)", parsed.normalized, result.status_code)

    async def _record_failure(self, parsed: ParsedURL, exc: FetchFailure) -> None:
        await self._repo.reserve_pending(
            url=parsed.original, normalized_url=parsed.normalized
        )
        await self._repo.mark_failed(
            parsed.normalized, FetchError(type=exc.kind, message=exc.message)
        )
        logger.warning("fetch failed for %s: %s/%s", parsed.normalized, exc.kind, exc.message)

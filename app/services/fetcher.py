"""Async HTTP fetcher: collects headers, cookies, and page source."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from typing import Iterable

import httpx

from app.models.schemas import CookieRecord
from app.utils.url import ParsedURL

TIMEOUT_SECONDS = 15.0
MAX_REDIRECTS = 5
MAX_BYTES = 5 * 1024 * 1024
USER_AGENT = "HTTPMetadataInventory/1.0"

logger = logging.getLogger(__name__)


class FetchFailure(Exception):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True, slots=True)
class FetchResult:
    status_code: int
    final_url: str
    headers: dict[str, str]
    cookies: list[CookieRecord]
    page_source: str
    content_type: str | None
    content_length: int
    truncated: bool
    fetched_at: datetime


def _headers_to_dict(headers: httpx.Headers) -> dict[str, str]:
    bucket: dict[str, list[str]] = {}
    for key, value in headers.multi_items():
        bucket.setdefault(key, []).append(value)
    return {k: ", ".join(v) for k, v in sorted(bucket.items(), key=lambda kv: kv[0].lower())}


def _parse_set_cookie(values: Iterable[str]) -> list[CookieRecord]:
    records: list[CookieRecord] = []
    for raw in values:
        try:
            jar = SimpleCookie()
            jar.load(raw)
            for name, morsel in jar.items():
                expires = morsel["expires"] or None
                expires_int: int | None
                try:
                    expires_int = int(expires) if expires and str(expires).isdigit() else None
                except (TypeError, ValueError):
                    expires_int = None
                records.append(
                    CookieRecord(
                        name=name,
                        value=morsel.value,
                        domain=morsel["domain"] or None,
                        path=morsel["path"] or None,
                        expires=expires_int,
                        secure=bool(morsel["secure"]),
                        httpOnly=bool(morsel["httponly"]),
                    )
                )
        except Exception as exc:
            logger.warning("cookie parse failed: %s", exc)
    return records


class Fetcher:
    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(TIMEOUT_SECONDS),
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            headers={"User-Agent": USER_AGENT},
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch(self, parsed: ParsedURL) -> FetchResult:
        try:
            async with self._client.stream("GET", parsed.normalized) as response:
                page_source, content_length, truncated = await self._read_capped_body(response)
        except httpx.TimeoutException as exc:
            raise FetchFailure("timeout", str(exc) or repr(exc)) from exc
        except httpx.TooManyRedirects as exc:
            raise FetchFailure("too_many_redirects", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise FetchFailure(exc.__class__.__name__, str(exc) or repr(exc)) from exc

        headers = _headers_to_dict(response.headers)
        set_cookie_values = response.headers.get_list("set-cookie", split_commas=False)
        cookies = _parse_set_cookie(set_cookie_values)

        return FetchResult(
            status_code=response.status_code,
            final_url=str(response.url),
            headers=headers,
            cookies=cookies,
            page_source=page_source,
            content_type=response.headers.get("content-type"),
            content_length=content_length,
            truncated=truncated,
            fetched_at=datetime.now(timezone.utc),
        )

    async def _read_capped_body(self, response: httpx.Response) -> tuple[str, int, bool]:
        chunks: list[bytes] = []
        total = 0
        truncated = False
        async for chunk in response.aiter_bytes():
            if total + len(chunk) > MAX_BYTES:
                remaining = MAX_BYTES - total
                if remaining > 0:
                    chunks.append(chunk[:remaining])
                    total += remaining
                truncated = True
                break
            chunks.append(chunk)
            total += len(chunk)

        body = b"".join(chunks)
        encoding = response.encoding or "utf-8"
        try:
            text = body.decode(encoding, errors="replace")
        except LookupError:
            text = body.decode("utf-8", errors="replace")
        return text, total, truncated

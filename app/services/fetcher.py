"""Async HTTP fetcher used to collect headers, cookies, and page source.

Uses `httpx.AsyncClient` for redirects, decoding, and connection pooling.
Defends against runaway responses with a hard byte cap and configurable
timeouts, and refuses private targets when the SSRF guard is on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from typing import Iterable

import httpx

from app.core.config import Settings
from app.models.schemas import CookieRecord
from app.utils.url import ParsedURL, is_private_host

logger = logging.getLogger(__name__)


class FetchFailure(Exception):
    """Raised by :class:`Fetcher` when a fetch can't be completed."""

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
    # Repeated headers are joined with ", " (RFC 7230 §3.2.2). Keys are
    # lower-cased and sorted so two equal responses produce equal documents.
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
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "fetcher.cookie_parse_failed",
                extra={"raw_preview": raw[:120], "error": str(exc)},
            )
    return records


class Fetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.fetch_timeout_s),
            follow_redirects=True,
            max_redirects=settings.fetch_max_redirects,
            headers={"User-Agent": settings.fetch_user_agent},
            http2=False,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch(self, parsed: ParsedURL) -> FetchResult:
        if (
            self._settings.block_private_networks
            and is_private_host(parsed.host)
        ):
            raise FetchFailure(
                "ssrf_blocked",
                f"Refusing to fetch private/loopback host {parsed.host!r}",
            )

        try:
            async with self._client.stream("GET", parsed.normalized) as response:
                page_source, content_length, truncated = await self._read_capped_body(
                    response
                )
        except httpx.TimeoutException as exc:
            raise FetchFailure("timeout", str(exc) or repr(exc)) from exc
        except httpx.TooManyRedirects as exc:
            raise FetchFailure("too_many_redirects", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise FetchFailure(exc.__class__.__name__, str(exc) or repr(exc)) from exc

        headers = _headers_to_dict(response.headers)
        # Set-Cookie is the one header that legitimately repeats; pull every
        # occurrence so we don't lose cookies.
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

    async def _read_capped_body(
        self, response: httpx.Response
    ) -> tuple[str, int, bool]:
        """Stream the body up to ``fetch_max_bytes`` and stop.

        Returns ``(text, byte_count, truncated)``. Decoding uses the encoding
        httpx infers, with replacement on bad bytes so we never raise on a
        broken charset.
        """
        cap = self._settings.fetch_max_bytes
        chunks: list[bytes] = []
        total = 0
        truncated = False
        async for chunk in response.aiter_bytes():
            if total + len(chunk) > cap:
                remaining = cap - total
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

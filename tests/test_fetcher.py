"""Tests for the HTTP fetcher (httpx + respx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.services.fetcher import MAX_BYTES, FetchFailure, Fetcher
from app.utils.url import normalize_url


@pytest.mark.asyncio
async def test_fetch_returns_headers_cookies_and_body(
    fetcher: Fetcher,
    respx_mock: respx.MockRouter,
    example_html: str,
) -> None:
    respx_mock.get("https://example.com/").mock(
        return_value=httpx.Response(
            200,
            content=example_html.encode(),
            headers=[
                ("Content-Type", "text/html; charset=utf-8"),
                ("X-Custom", "value"),
                ("Set-Cookie", "session=abc123; Path=/; HttpOnly; Secure"),
                ("Set-Cookie", "tracker=xyz; Path=/"),
            ],
        )
    )

    parsed = normalize_url("https://example.com/")
    result = await fetcher.fetch(parsed)

    assert result.status_code == 200
    assert result.final_url == "https://example.com/"
    assert "content-type" in {k.lower() for k in result.headers}
    assert any(
        c.name == "session" and c.value == "abc123" and c.secure and c.http_only
        for c in result.cookies
    )
    assert any(c.name == "tracker" for c in result.cookies)
    assert result.page_source == example_html
    assert result.truncated is False


@pytest.mark.asyncio
async def test_fetch_truncates_oversized_body(
    fetcher: Fetcher,
    respx_mock: respx.MockRouter,
) -> None:
    big_body = b"A" * (MAX_BYTES + 4096)
    respx_mock.get("https://example.com/big").mock(
        return_value=httpx.Response(
            200,
            content=big_body,
            headers={"Content-Type": "application/octet-stream"},
        )
    )

    parsed = normalize_url("https://example.com/big")
    result = await fetcher.fetch(parsed)

    assert result.truncated is True
    assert result.content_length == MAX_BYTES


@pytest.mark.asyncio
async def test_fetch_follows_redirects_to_final_url(
    fetcher: Fetcher,
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get("https://example.com/old").mock(
        return_value=httpx.Response(301, headers={"Location": "https://example.com/new"})
    )
    respx_mock.get("https://example.com/new").mock(
        return_value=httpx.Response(200, text="OK")
    )

    parsed = normalize_url("https://example.com/old")
    result = await fetcher.fetch(parsed)

    assert result.status_code == 200
    assert result.final_url == "https://example.com/new"


@pytest.mark.asyncio
async def test_fetch_raises_on_timeout(
    fetcher: Fetcher,
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get("https://example.com/slow").mock(
        side_effect=httpx.ReadTimeout("read timed out")
    )

    parsed = normalize_url("https://example.com/slow")
    with pytest.raises(FetchFailure) as exc_info:
        await fetcher.fetch(parsed)
    assert exc_info.value.kind == "timeout"


@pytest.mark.asyncio
async def test_fetch_raises_on_too_many_redirects(
    fetcher: Fetcher,
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get("https://example.com/loop").mock(
        side_effect=httpx.TooManyRedirects("loop")
    )
    parsed = normalize_url("https://example.com/loop")
    with pytest.raises(FetchFailure) as exc_info:
        await fetcher.fetch(parsed)
    assert exc_info.value.kind == "too_many_redirects"

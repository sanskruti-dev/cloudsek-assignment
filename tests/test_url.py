"""Tests for URL normalisation and validation."""

from __future__ import annotations

import pytest

from app.utils.url import InvalidURLError, normalize_url


def test_normalize_lowercases_scheme_and_host() -> None:
    parsed = normalize_url("HTTPS://Example.COM/Path")
    assert parsed.scheme == "https"
    assert parsed.host == "example.com"
    assert parsed.normalized == "https://example.com/Path"


def test_normalize_strips_default_ports() -> None:
    assert normalize_url("http://example.com:80/").normalized == "http://example.com/"
    assert normalize_url("https://example.com:443/").normalized == "https://example.com/"


def test_normalize_keeps_non_default_port() -> None:
    parsed = normalize_url("http://example.com:8080/foo")
    assert parsed.normalized == "http://example.com:8080/foo"
    assert parsed.port == 8080


def test_normalize_drops_fragment() -> None:
    parsed = normalize_url("https://example.com/path?a=1#section")
    assert parsed.normalized == "https://example.com/path?a=1"


def test_normalize_adds_trailing_slash_when_path_missing() -> None:
    assert normalize_url("https://example.com").normalized == "https://example.com/"


def test_normalize_preserves_query_order() -> None:
    parsed = normalize_url("https://example.com/?b=2&a=1")
    assert parsed.normalized == "https://example.com/?b=2&a=1"


def test_normalize_rejects_blank_input() -> None:
    with pytest.raises(InvalidURLError):
        normalize_url("")


def test_normalize_rejects_missing_scheme() -> None:
    with pytest.raises(InvalidURLError):
        normalize_url("example.com/path")


def test_normalize_rejects_missing_host() -> None:
    with pytest.raises(InvalidURLError):
        normalize_url("https:///path")


def test_normalize_rejects_unsupported_scheme() -> None:
    with pytest.raises(InvalidURLError):
        normalize_url("ftp://example.com/")


def test_normalize_is_idempotent() -> None:
    once = normalize_url("HTTPS://Example.com:443/foo#bar").normalized
    twice = normalize_url(once).normalized
    assert once == twice

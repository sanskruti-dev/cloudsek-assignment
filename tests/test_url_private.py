"""Tests for private-network detection used by the SSRF guard."""

from __future__ import annotations

import socket
from typing import Any
from unittest.mock import patch

import pytest

from app.utils.url import is_private_host


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.1.2",  # link-local
        "::1",
        "fc00::1",  # unique local
    ],
)
def test_is_private_host_recognises_private_ips(host: str) -> None:
    assert is_private_host(host) is True


def test_is_private_host_returns_false_for_public_ip() -> None:
    assert is_private_host("8.8.8.8") is False


def test_is_private_host_resolves_dns_names() -> None:
    fake_addrinfo: list[Any] = [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0)),
    ]
    with patch("app.utils.url.socket.getaddrinfo", return_value=fake_addrinfo):
        assert is_private_host("public.example.test") is False

    fake_private: list[Any] = [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0)),
    ]
    with patch("app.utils.url.socket.getaddrinfo", return_value=fake_private):
        assert is_private_host("evil.example.test") is True


def test_is_private_host_returns_false_on_dns_failure() -> None:
    with patch(
        "app.utils.url.socket.getaddrinfo", side_effect=socket.gaierror
    ):
        assert is_private_host("nonexistent.example.test") is False

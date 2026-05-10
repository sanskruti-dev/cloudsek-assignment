"""URL parsing, validation, and normalisation.

Normalisation is conservative: lowercase scheme/host, strip default ports,
drop the fragment, ensure a trailing slash on empty paths. Query parameters
are kept as-is because reordering them can change semantics.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit

DEFAULT_PORTS = {"http": 80, "https": 443}


class InvalidURLError(ValueError):
    """Raised when a URL cannot be parsed or violates policy."""


@dataclass(frozen=True, slots=True)
class ParsedURL:
    original: str
    normalized: str
    scheme: str
    host: str
    port: int | None


def normalize_url(url: str, *, allowed_schemes: frozenset[str] | None = None) -> ParsedURL:
    """Parse and normalise ``url``. Raises :class:`InvalidURLError` on failure."""
    if not isinstance(url, str) or not url.strip():
        raise InvalidURLError("URL must be a non-empty string")

    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if not scheme:
        raise InvalidURLError("URL must include a scheme (e.g. https://)")
    if allowed_schemes is not None and scheme not in allowed_schemes:
        raise InvalidURLError(
            f"Scheme {scheme!r} not in allow-list {sorted(allowed_schemes)}"
        )

    if not parts.hostname:
        raise InvalidURLError("URL must include a host")

    host = parts.hostname.lower()
    port = parts.port

    if port is None or port == DEFAULT_PORTS.get(scheme):
        authority = host
        canonical_port: int | None = None
    else:
        authority = f"{host}:{port}"
        canonical_port = port

    path = parts.path or "/"
    path = quote(path, safe="/%:@!$&'()*+,;=-._~")

    normalized = urlunsplit((scheme, authority, path, parts.query, ""))
    return ParsedURL(
        original=url,
        normalized=normalized,
        scheme=scheme,
        host=host,
        port=canonical_port,
    )


def is_private_host(host: str) -> bool:
    """Return True if ``host`` (literal or DNS name) maps to a private IP.

    Used by the SSRF guard. We resolve every A/AAAA record so that a public
    DNS name aliased to a private IP is still caught.
    """
    candidates: list[str] = []
    try:
        ipaddress.ip_address(host)
        candidates.append(host)
    except ValueError:
        try:
            for family, _, _, _, sockaddr in socket.getaddrinfo(
                host, None, type=socket.SOCK_STREAM
            ):
                if family in (socket.AF_INET, socket.AF_INET6):
                    candidates.append(sockaddr[0])
        except socket.gaierror:
            return False

    for raw in candidates:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True
    return False

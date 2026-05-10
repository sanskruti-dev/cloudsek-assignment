"""URL parsing and normalisation."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})
DEFAULT_PORTS = {"http": 80, "https": 443}


class InvalidURLError(ValueError):
    """Raised when a URL cannot be parsed or uses an unsupported scheme."""


@dataclass(frozen=True, slots=True)
class ParsedURL:
    original: str
    normalized: str
    scheme: str
    host: str
    port: int | None


def normalize_url(url: str) -> ParsedURL:
    if not isinstance(url, str) or not url.strip():
        raise InvalidURLError("URL must be a non-empty string")

    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if not scheme:
        raise InvalidURLError("URL must include a scheme (e.g. https://)")
    if scheme not in ALLOWED_SCHEMES:
        raise InvalidURLError(f"Unsupported URL scheme: {scheme!r}")
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

    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    normalized = urlunsplit((scheme, authority, path, parts.query, ""))
    return ParsedURL(
        original=url,
        normalized=normalized,
        scheme=scheme,
        host=host,
        port=canonical_port,
    )

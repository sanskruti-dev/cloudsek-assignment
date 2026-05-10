"""Tests for the Settings model."""

from __future__ import annotations

import pytest

from app.core.config import Settings


def test_log_level_normalised_to_uppercase() -> None:
    settings = Settings(log_level="info", ALLOWED_SCHEMES="https")
    assert settings.log_level == "INFO"


def test_invalid_log_level_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(log_level="chatty", ALLOWED_SCHEMES="https")


def test_api_prefix_normalised() -> None:
    assert Settings(api_prefix="api/v1").api_prefix == "/api/v1"
    assert Settings(api_prefix="/api/v1/").api_prefix == "/api/v1"
    assert Settings(api_prefix="/").api_prefix == ""


def test_allowed_schemes_parsed() -> None:
    settings = Settings(ALLOWED_SCHEMES="HTTP, HTTPS , gopher")
    assert settings.allowed_schemes == frozenset({"http", "https", "gopher"})

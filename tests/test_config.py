"""Tests for the Settings model."""

from __future__ import annotations

from app.core.config import Settings, get_settings


def test_defaults_are_sensible() -> None:
    settings = Settings()
    assert settings.mongo_uri.startswith("mongodb://")
    assert settings.mongo_db == "metadata_inventory"
    assert settings.mongo_collection == "url_metadata"


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_env_vars_are_picked_up(monkeypatch) -> None:
    monkeypatch.setenv("MONGO_DB", "custom_db")
    settings = Settings()
    assert settings.mongo_db == "custom_db"

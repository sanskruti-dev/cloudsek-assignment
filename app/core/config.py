"""Application settings, sourced from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "metadata_inventory"
    mongo_collection: str = "url_metadata"
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

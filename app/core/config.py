"""Application settings.

Values come from env vars first, then `.env` (dev only), then class defaults.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "http-metadata-inventory"
    app_env: str = Field(default="development")
    log_level: str = Field(default="INFO")

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_prefix: str = "/api/v1"

    # MongoDB
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "metadata_inventory"
    mongo_collection: str = "url_metadata"
    mongo_connect_timeout_ms: int = 5000
    mongo_server_selection_timeout_ms: int = 5000
    mongo_startup_retry_attempts: int = 30
    mongo_startup_retry_delay_s: float = 2.0

    # HTTP fetcher
    fetch_timeout_s: float = 15.0
    fetch_max_redirects: int = 5
    fetch_max_bytes: int = 5 * 1024 * 1024
    fetch_user_agent: str = (
        "HTTPMetadataInventory/1.0 (+https://example.com/bot)"
    )

    # SSRF guard. Off in docker-compose so reviewers can hit any URL; keep on
    # for real deployments.
    block_private_networks: bool = True
    allowed_schemes_csv: Annotated[
        str,
        Field(alias="ALLOWED_SCHEMES"),
    ] = "http,https"

    @field_validator("log_level")
    @classmethod
    def _normalise_log_level(cls, value: str) -> str:
        normalised = value.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalised not in allowed:
            raise ValueError(
                f"LOG_LEVEL must be one of {sorted(allowed)}, got {value!r}"
            )
        return normalised

    @field_validator("api_prefix")
    @classmethod
    def _normalise_prefix(cls, value: str) -> str:
        if not value:
            return ""
        cleaned = "/" + value.strip().strip("/")
        return "" if cleaned == "/" else cleaned

    @property
    def allowed_schemes(self) -> frozenset[str]:
        return frozenset(
            scheme.strip().lower()
            for scheme in self.allowed_schemes_csv.split(",")
            if scheme.strip()
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

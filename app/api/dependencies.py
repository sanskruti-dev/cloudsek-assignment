"""FastAPI dependency providers. Long-lived singletons live on ``app.state``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, Request

from app.core.config import Settings, get_settings

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance
    from app.db.repository import MetadataRepository
    from app.services.fetcher import Fetcher
    from app.services.metadata_service import MetadataService
    from app.services.worker import BackgroundTaskScheduler


def settings_provider() -> Settings:
    return get_settings()


def get_repository(request: Request) -> "MetadataRepository":
    return request.app.state.repository  # type: ignore[no-any-return]


def get_service(request: Request) -> "MetadataService":
    return request.app.state.service  # type: ignore[no-any-return]


def get_fetcher(request: Request) -> "Fetcher":
    return request.app.state.fetcher  # type: ignore[no-any-return]


def get_scheduler(request: Request) -> "BackgroundTaskScheduler":
    return request.app.state.scheduler  # type: ignore[no-any-return]


SettingsDep = Depends(settings_provider)

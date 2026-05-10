"""FastAPI dependency providers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from app.services.metadata_service import MetadataService


def get_service(request: Request) -> "MetadataService":
    return request.app.state.service

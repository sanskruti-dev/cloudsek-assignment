"""Health probe."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from pymongo.errors import PyMongoError

from app import __version__
from app.api.dependencies import settings_provider
from app.core.config import Settings
from app.models.schemas import HealthResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get(
    "/health-check",
    response_model=HealthResponse,
    summary="Liveness/readiness probe",
)
async def health_check(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(settings_provider)],
) -> HealthResponse:
    mongo_state = "unknown"
    client = getattr(request.app.state, "mongo_client", None)
    if client is None:
        mongo_state = "uninitialised"
    else:
        try:
            await client.admin.command("ping")
            mongo_state = "ok"
        except PyMongoError as exc:
            mongo_state = f"error:{exc.__class__.__name__}"
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status="ok" if mongo_state == "ok" else "degraded",
        app=settings.app_name,
        version=__version__,
        mongo=mongo_state,
    )

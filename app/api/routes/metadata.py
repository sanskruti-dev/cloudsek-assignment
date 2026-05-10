"""Metadata endpoints: synchronous POST and cache-aware GET."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.dependencies import get_service
from app.models.schemas import (
    MetadataAcceptedResponse,
    MetadataCreateRequest,
    MetadataRecord,
    MetadataStatus,
)
from app.services.fetcher import FetchFailure
from app.services.metadata_service import MetadataService
from app.utils.url import InvalidURLError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/metadata", tags=["metadata"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=MetadataRecord,
    responses={
        400: {"description": "Invalid URL"},
        502: {"description": "Upstream fetch failed"},
    },
    summary="Collect and store metadata for a URL (synchronous)",
)
async def create_metadata(
    payload: MetadataCreateRequest,
    service: Annotated[MetadataService, Depends(get_service)],
) -> MetadataRecord:
    try:
        return await service.collect_now(str(payload.url))
    except InvalidURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FetchFailure as exc:
        raise HTTPException(
            status_code=502,
            detail={"kind": exc.kind, "message": exc.message},
        ) from exc


@router.get(
    "",
    response_model=MetadataRecord | MetadataAcceptedResponse,
    responses={
        200: {"description": "Cached metadata returned"},
        202: {
            "description": "Cache miss - collection scheduled in the background",
            "model": MetadataAcceptedResponse,
        },
        400: {"description": "Invalid URL"},
    },
    summary="Retrieve metadata for a URL (cached or scheduled on miss)",
)
async def read_metadata(
    response: Response,
    service: Annotated[MetadataService, Depends(get_service)],
    url: Annotated[str, Query(description="The URL whose metadata to retrieve")],
) -> MetadataRecord | MetadataAcceptedResponse:
    try:
        record, served_from_cache = await service.get_or_schedule(url)
    except InvalidURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if served_from_cache and record.status == MetadataStatus.COMPLETE:
        response.status_code = status.HTTP_200_OK
        return record

    if served_from_cache and record.status == MetadataStatus.FAILED:
        response.status_code = status.HTTP_200_OK
        return record

    response.status_code = status.HTTP_202_ACCEPTED
    return MetadataAcceptedResponse(
        url=record.url,
        normalized_url=record.normalized_url,
        status=record.status,
    )

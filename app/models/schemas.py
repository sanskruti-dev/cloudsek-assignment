"""Pydantic models for the API and persistence layer."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
)


class MetadataStatus(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"
    FAILED = "failed"


class CookieRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str
    value: str
    domain: str | None = None
    path: str | None = None
    expires: int | None = None
    secure: bool = False
    http_only: bool = Field(default=False, alias="httpOnly")


class FetchError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    message: str


class MetadataCreateRequest(BaseModel):
    """Request body for ``POST /metadata``."""

    model_config = ConfigDict(extra="forbid")

    url: Annotated[AnyHttpUrl, Field(description="HTTP(S) URL to fetch")]

    @field_serializer("url")
    def _serialize_url(self, value: AnyHttpUrl) -> str:
        return str(value)


class MetadataRecord(BaseModel):
    """Persisted document, also returned by the API."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    url: str
    normalized_url: str
    status: MetadataStatus
    status_code: int | None = None
    final_url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    cookies: list[CookieRecord] = Field(default_factory=list)
    page_source: str | None = None
    content_type: str | None = None
    content_length: int | None = None
    truncated: bool = False
    error: FetchError | None = None
    created_at: datetime
    updated_at: datetime
    fetched_at: datetime | None = None


class MetadataAcceptedResponse(BaseModel):
    """Body returned with ``202 Accepted`` while collection is in progress."""

    model_config = ConfigDict(extra="forbid")

    url: str
    normalized_url: str
    status: MetadataStatus
    detail: str = (
        "Metadata is being collected in the background. "
        "Retry GET in a few moments to receive the full record."
    )


def fetched_record_to_dict(record: MetadataRecord) -> dict[str, Any]:
    return record.model_dump(mode="python")

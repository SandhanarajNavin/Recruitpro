"""Upload and ingestion-status contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import ORMModel


class ResumeOut(ORMModel):
    id: uuid.UUID
    candidate_id: uuid.UUID
    original_filename: str
    content_type: str
    size_bytes: int
    version: int
    status: str
    error: str | None = None
    created_at: datetime
    processed_at: datetime | None = None


class RejectedUpload(BaseModel):
    filename: str
    reason: str


class UploadAccepted(BaseModel):
    """202 body. Ingestion continues on a worker; poll the status endpoint."""

    accepted: list[ResumeOut]
    rejected: list[RejectedUpload]


class ResumeStatusOut(ORMModel):
    id: uuid.UUID
    status: str
    error: str | None = None
    candidate_id: uuid.UUID
    processed_at: datetime | None = None

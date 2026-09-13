"""Application wire contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class ApplicationCreateRequest(BaseModel):
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    #: Where the candidate came from, when a screening surfaced them.
    screening_result_id: uuid.UUID | None = None
    #: Opening straight into a later stage — "shortlist" skips applied/screening.
    stage: str | None = None


class ApplicationMoveRequest(BaseModel):
    to_stage: str
    #: Stored as the rejection reason when moving to rejected.
    note: str | None = Field(default=None, max_length=2000)


class ApplicationEventOut(ORMModel):
    from_stage: str | None
    to_stage: str
    note: str | None
    created_at: datetime


class ApplicationOut(ORMModel):
    id: uuid.UUID
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    stage: str
    match_score_at_entry: float | None = None
    rejection_reason: str | None = None
    closed_at: datetime | None = None
    created_at: datetime


class ApplicationDetail(BaseModel):
    application: ApplicationOut
    candidate_name: str
    job_title: str
    events: list[ApplicationEventOut]


class ApplicationRow(BaseModel):
    """One line in a pipeline board or job's candidate list."""

    id: uuid.UUID
    candidate_id: uuid.UUID
    candidate_name: str
    job_id: uuid.UUID
    job_title: str
    stage: str
    match_score_at_entry: float | None = None
    created_at: datetime


class PipelineOut(BaseModel):
    """Stage counts in display order. Every stage present, including zeros."""

    stages: dict[str, int]
    total: int

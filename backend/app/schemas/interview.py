"""Interview scheduling contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class InterviewOut(BaseModel):
    """One interview with the context a list needs to be actionable."""

    id: uuid.UUID
    application_id: uuid.UUID
    candidate_id: uuid.UUID
    candidate_name: str
    job_id: uuid.UUID
    job_title: str
    scheduled_at: datetime
    kind: str | None = None
    interviewer: str | None = None
    outcome: str
    notes: str | None = None
    #: The application's pipeline stage, which the outcome does not imply.
    stage: str


class InterviewListResponse(BaseModel):
    items: list[InterviewOut]
    #: Size of each window, so a tab can show its count before being opened.
    counts: dict[str, int]


class InterviewCreateRequest(BaseModel):
    application_id: uuid.UUID
    scheduled_at: datetime
    kind: str | None = Field(default=None, max_length=60)
    interviewer: str | None = Field(default=None, max_length=160)
    #: Also move the application to the interview stage, so the booking shows up
    #: in the pipeline. On by default: booking an interview usually *is* the
    #: decision. Turn it off to hold a slot before committing.
    advance_stage: bool = True


class InterviewUpdateRequest(BaseModel):
    outcome: str | None = Field(
        default=None, pattern="^(scheduled|completed|cancelled|no_show)$"
    )
    notes: str | None = None
    scheduled_at: datetime | None = None

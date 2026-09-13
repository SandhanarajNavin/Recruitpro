"""Operator dashboard contract."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.schemas.job import JobOut


class MetricOut(BaseModel):
    """One headline card. ``delta_pct`` is null when there is no prior window to
    compare against, so the UI can omit the trend rather than invent one."""

    key: str
    label: str
    value: int
    delta_pct: float | None = None
    #: Line of context under the number — not always a delta.
    hint: str | None = None
    #: neutral | up | down | warn
    tone: str = "neutral"


class RoleSliceOut(BaseModel):
    role: str
    count: int
    share: float


class DayCountOut(BaseModel):
    day: date
    count: int


class JobCardOut(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    match_count: int
    status: str


class RecentCandidateOut(BaseModel):
    candidate_id: uuid.UUID
    name: str
    role: str | None = None
    applied_at: datetime
    #: Screening verdict where one exists, otherwise the ingestion state.
    status: str


class MatchedCandidateOut(BaseModel):
    candidate_id: uuid.UUID
    name: str
    role: str | None = None
    score: float
    years: float


class AttentionItemOut(BaseModel):
    job_id: uuid.UUID
    title: str
    #: review | matches | find | decide — drives the call to action.
    kind: str
    detail: str
    count: int


class ActivityItemOut(BaseModel):
    kind: str
    text: str
    at: datetime
    href: str | None = None
    #: Second line — the job or role the event concerns.
    context: str | None = None


class ReviewCandidateOut(BaseModel):
    candidate_id: uuid.UUID
    name: str
    email: str | None = None
    role: str | None = None
    #: Null when never screened, which is not the same as scoring zero.
    match_score: float | None = None
    years: float
    status: str
    #: Best screening verdict, or null when never screened.
    recommendation: str | None = None


class OpenJobRowOut(BaseModel):
    id: uuid.UUID
    title: str
    location: str | None = None
    candidate_count: int
    strong_match_count: int
    days_open: int
    status: str
    #: needs_candidates | needs_review | screening, else the stored status.
    attention_status: str


class DayActivityOut(BaseModel):
    day: date
    applied: int
    hired: int


class DashboardResponse(BaseModel):
    mode: str
    embedding_model: str
    weights: dict[str, float]
    funnel_limits: dict[str, int]
    stats: dict[str, int]
    recent_jobs: list[JobOut]

    # Added for the dashboard redesign. Optional with empty defaults so any older
    # client that ignores them keeps working unchanged.
    metrics: list[MetricOut] = []
    candidates_by_role: list[RoleSliceOut] = []
    candidates_added: list[DayCountOut] = []
    job_cards: list[JobCardOut] = []
    recent_candidates: list[RecentCandidateOut] = []
    top_matched: list[MatchedCandidateOut] = []

    # Second redesign: attention queue, pipeline strip, activity feed and the two
    # tables. Defaulted so an older client that ignores them keeps working.
    attention: list[AttentionItemOut] = []
    pipeline: dict[str, int] = {}
    activity: list[ActivityItemOut] = []
    review_queue: list[ReviewCandidateOut] = []
    open_jobs: list[OpenJobRowOut] = []
    #: Percent change in entries per stage over the selected window. Null for a
    #: stage nothing entered in the prior window.
    pipeline_deltas: dict[str, float | None] = {}
    #: Length of that window, echoed back so the UI can label it.
    window_days: int = 7
    #: Applications opened vs hires made per day, zero-filled.
    weekly_activity: list[DayActivityOut] = []

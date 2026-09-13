"""Screening contracts — a matching run and its shortlist.

Separate from the AI schemas on purpose: what a model is asked to produce and what
the web app consumes change for different reasons, and coupling them means a prompt
tweak becomes a frontend break.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class ScreeningCreateRequest(BaseModel):
    job_id: uuid.UUID


class FunnelOut(BaseModel):
    """Stage widths for this run — what the funnel diagram shows, per screening."""

    pool: int
    filtered: int
    retrieved: int
    reranked: int
    evaluated: int
    shortlisted: int


class ScreeningOut(ORMModel):
    id: uuid.UUID
    job_id: uuid.UUID
    status: str
    stage: str
    mode: str
    weights: dict[str, float]
    degradations: list[str]
    panel_summary: str | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ScreeningStatusResponse(BaseModel):
    screening: ScreeningOut
    funnel: FunnelOut


class SubscoreOut(BaseModel):
    category: str
    label: str
    score: float
    weight: float
    contribution: float


class ExplanationOut(ORMModel):
    why_match: list[str]
    why_not: list[str]
    verdict: str | None
    model: str


class RankedCandidateOut(BaseModel):
    rank: int
    candidate_id: uuid.UUID
    name: str
    #: Identity line under the name on the results screen. All optional: a
    #: candidate whose resume never yielded a role or a location still ranks.
    role: str | None = None
    years: float | None = None
    location: str | None = None
    composite_score: float
    recommendation: str
    effective_recommendation: str
    shortlisted: bool
    subscores: list[SubscoreOut]
    matched_skills: list[str]
    missing_skills: list[str]
    retrieval_score: float | None
    rerank_score: float | None
    evidence: dict
    explanation: ExplanationOut | None
    override_note: str | None = None


class ScreeningResultsResponse(BaseModel):
    screening: ScreeningOut
    funnel: FunnelOut
    shortlist: list[RankedCandidateOut]
    also_considered: list[RankedCandidateOut]


class OverrideRequest(BaseModel):
    recommendation: str = Field(pattern="^(strong_hire|interview|maybe|pass)$")
    note: str | None = None


# ── search sessions (architecture doc §13) ────────────────────────────────
class SearchCreateRequest(BaseModel):
    job_id: uuid.UUID
    #: How many profiles per page. Defaults to the configured shortlist size.
    page_size: int | None = Field(default=None, ge=1, le=50)


class SearchCandidateOut(BaseModel):
    rank: int
    candidate_id: uuid.UUID
    name: str
    score: float
    primary_role: str | None = None
    shown_at: datetime | None = None


class SearchPageResponse(BaseModel):
    """One page of a session. ``remaining`` is what drives a 'show more' control."""

    session_id: uuid.UUID
    job_id: uuid.UUID
    #: The screening this ranking came from. Without it a caller has the candidates
    #: but no way to reach their evidence or record an override against them.
    screening_id: uuid.UUID | None = None
    status: str
    page_size: int
    total: int
    remaining: int
    relaxations: list[str]
    candidates: list[SearchCandidateOut]

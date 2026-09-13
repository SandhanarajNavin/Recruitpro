"""Candidate repository contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel
# SubscoreOut/ExplanationOut come from the screening contracts: a candidate's
# best-fit jobs show the very same breakdown, and a parallel copy would drift.
from app.schemas.matching import ExplanationOut, SubscoreOut
from app.schemas.resume import ResumeOut


class ProfileOut(ORMModel):
    id: uuid.UUID
    current_title: str | None
    total_years_experience: float
    seniority_rank: int
    skills: list[str]
    domains: list[str]
    education: list[str]
    certifications: list[str]
    achievements: list[str]
    projects: list[str]
    experience: list[dict]
    parser_model: str
    version: int


class CandidateOut(ORMModel):
    id: uuid.UUID
    full_name: str
    email: str | None
    phone: str | None
    location: str | None
    summary: str | None
    #: Recruiter's own notes, distinct from the parsed summary.
    notes: str | None = None
    status: str
    created_at: datetime


class CandidateListItem(CandidateOut):
    current_title: str | None = None
    total_years_experience: float = 0.0
    skills: list[str] = Field(default_factory=list)
    resume_status: str | None = None
    primary_role: str | None = None
    #: Best composite score this candidate has achieved across every screening the
    #: recruiter has run. Null when they have never been screened — which is a
    #: different statement from scoring zero.
    best_match_score: float | None = None
    #: Furthest pipeline stage reached, across all jobs. Null when no application
    #: has been opened for them.
    stage: str | None = None


class CandidateSummary(BaseModel):
    """Pool-level counts for the tiles above the list. Not page-scoped."""

    total: int
    new_today: int
    to_review: int
    shortlisted: int


class CandidateListResponse(BaseModel):
    items: list[CandidateListItem]
    total: int
    limit: int
    offset: int
    summary: CandidateSummary


class CandidateUpdateRequest(BaseModel):
    """Recruiter-editable fields.

    Parser output is not here: a reprocess rewrites it, so an edit would silently
    disappear on the next upload.
    """

    summary: str | None = None
    notes: str | None = None
    location: str | None = Field(default=None, max_length=160)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, pattern="^(active|archived)$")


class BestMatchOut(BaseModel):
    """Highest screening score, with the job it was measured against.

    A score without its job says nothing a recruiter can act on.
    """

    score: float
    recommendation: str
    job_id: uuid.UUID
    job_title: str
    screening_id: uuid.UUID


class JobMatchOut(BaseModel):
    """One job this candidate has been scored against.

    Carries the same breakdown the job page shows for the same pair — composite,
    subscores, matched and missing skills, evidence and explanation — so the two
    views cannot disagree. ``score`` is what the scoring engine produced for that
    screening, never a fit estimated here.
    """

    job_id: uuid.UUID
    job_title: str
    job_status: str
    department: str | None = None
    location: str | None = None
    score: float
    recommendation: str
    screening_id: uuid.UUID
    screened_at: datetime | None = None
    shortlisted: bool
    #: Where they placed in that run.
    rank: int
    subscores: list[SubscoreOut] = []
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    evidence: dict = {}
    explanation: ExplanationOut | None = None


class CandidateDetailResponse(BaseModel):
    candidate: CandidateOut
    profile: ProfileOut | None
    resumes: list[ResumeOut]
    #: Null when nobody has screened this candidate — which is not a score of zero.
    best_match: BestMatchOut | None = None
    #: Profile skills bucketed for display, in display order.
    skill_groups: dict[str, list[str]] = {}
    #: Open applications, so the page can show where they stand.
    applications: list[dict] = []
    #: Best-fitting jobs, highest score first.
    job_matches: list[JobMatchOut] = []
    #: Owner's jobs this candidate has never been screened against. Reported so the
    #: page can say the ranking covers only what has actually been run.
    unscored_jobs: int = 0

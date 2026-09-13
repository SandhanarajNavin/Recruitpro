"""Job description contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class JobCreateRequest(BaseModel):
    title: str | None = None
    description: str = Field(min_length=40)
    location: str | None = None


class ExtractedDescription(BaseModel):
    """Text read out of an uploaded JD file, before any job exists.

    Deliberately not a created job. The recruiter gets the text back to read and
    correct first, because extraction from a PDF is lossy in ways only they can see
    — a two-column layout interleaved, a table flattened, a header repeated on every
    page. Creating the job from unreviewed text would bake that into the parsed
    requirements and the JD vector.
    """

    text: str
    filename: str
    characters: int


class RequirementOut(ORMModel):
    seniority: str | None
    min_years_experience: int
    required_skills: list[dict]
    preferred_skills: list[str]
    domains: list[str]
    responsibilities: list[str]
    education: list[str]
    red_flags: list[str]
    hard_filters: dict
    parser_model: str


class JobOut(ORMModel):
    id: uuid.UUID
    title: str
    description: str
    location: str | None
    status: str
    created_at: datetime
    department: str | None = None
    employment_type: str | None = None
    hiring_manager: str | None = None


class JobListItem(JobOut):
    """A job with the counts the list screen shows."""

    #: Distinct candidates any screening has matched to this job.
    candidate_count: int = 0
    #: Of those, how many scored in the top band.
    strong_match_count: int = 0
    #: Applications currently open against this job.
    in_pipeline: int = 0


class JobUpdateRequest(BaseModel):
    """Everything a recruiter may change after creation.

    The description is deliberately absent: it is what the requirements were parsed
    from, so editing it would leave the parsed requirements describing a job that
    no longer exists.
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=160)
    department: str | None = Field(default=None, max_length=120)
    employment_type: str | None = Field(default=None, max_length=60)
    hiring_manager: str | None = Field(default=None, max_length=160)
    status: str | None = Field(default=None, pattern="^(open|on_hold|closed)$")


class SkillCoverageOut(BaseModel):
    skill: str
    #: Candidates in the latest completed screening evidencing this skill.
    matched: int
    #: Candidates that screening evaluated — the denominator.
    of: int


class JobStatsOut(BaseModel):
    candidate_count: int = 0
    strong_match_count: int = 0
    in_pipeline: int = 0
    days_open: int = 0
    new_this_week: int = 0
    #: Most recent completed run, so the page can link straight to its results.
    latest_screening_id: uuid.UUID | None = None


class JobDetailResponse(BaseModel):
    job: JobOut
    requirement: RequirementOut | None
    stats: JobStatsOut = JobStatsOut()
    skill_coverage: list[SkillCoverageOut] = []

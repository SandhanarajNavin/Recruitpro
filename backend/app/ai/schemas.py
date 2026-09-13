"""Pydantic contracts for every AI output.

These are the *only* shapes the pipeline accepts from a model. The offline engine
produces the same objects, so nothing downstream knows or cares which engine ran.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── job description parsing ───────────────────────────────────────────────
class RequiredSkill(BaseModel):
    skill: str
    # 1-5, how load-bearing the requirement is. 5 means a missing skill should cap
    # the skills dimension no matter how strong the rest of the resume is.
    importance: int = Field(ge=1, le=5)


class ParsedJobDescription(BaseModel):
    title: str
    seniority: str
    min_years_experience: int = Field(ge=0, le=50)
    required_skills: list[RequiredSkill]
    preferred_skills: list[str]
    domains: list[str]
    responsibilities: list[str]
    education: list[str]
    red_flags: list[str]


# ── resume parsing ────────────────────────────────────────────────────────
class EmploymentEntry(BaseModel):
    """One employment span.

    Months matter: a resume that reads "Feb '25 - Present" loses most of its
    precision if only the year survives, and the total is computed from these
    fields rather than by the model.
    """

    title: str
    company: str
    start_year: int | None
    start_month: int | None = Field(default=None, ge=1, le=12)
    end_year: int | None
    end_month: int | None = Field(default=None, ge=1, le=12)
    #: True for "Present" / "Current" roles, so the end is resolved against today
    #: at computation time instead of being frozen into the record.
    is_current: bool = False
    highlights: list[str]


class ParsedResume(BaseModel):
    full_name: str | None
    email: str | None
    phone: str | None
    location: str | None
    current_title: str | None
    # Nullable so "unknown" is expressible. The prompt requires null rather than
    # 0 when dates exist but cannot be totalled — without this the response
    # fails validation and the whole parse falls back to the lexicon.
    total_years_experience: float | None = Field(default=None, ge=0, le=60)
    summary: str | None
    skills: list[str]
    domains: list[str]
    experience: list[EmploymentEntry]
    education: list[str]
    certifications: list[str]
    projects: list[str]
    achievements: list[str]


# ── requirement evaluation ────────────────────────────────────────────────
class RequirementVerdict(BaseModel):
    """One requirement, judged against one candidate.

    ``met`` is deliberately three-valued via ``confidence``: doc §12 requires
    "evidence unclear or insufficient" to be a first-class answer rather than a
    forced yes or no.
    """

    requirement: str
    category: str
    met: bool
    confidence: str = Field(pattern="^(high|medium|low)$")
    # Quoted or near-quoted from the resume. The explanation generator may use only
    # these strings, which is what stops it inventing qualifications.
    evidence: list[str]
    reasoning: str


class CategoryAssessment(BaseModel):
    category: str
    score: float = Field(ge=0, le=100)
    reasoning: str
    verdicts: list[RequirementVerdict]


class CandidateEvaluation(BaseModel):
    """The evaluator's full output for one candidate. Carries no overall score —
    the composite is computed in Python from these category scores."""

    categories: list[CategoryAssessment]
    matched_skills: list[str]
    missing_skills: list[str]
    strengths: list[str]
    concerns: list[str]


# ── explanation ───────────────────────────────────────────────────────────
class CandidateExplanation(BaseModel):
    why_match: list[str]
    why_not: list[str]
    verdict: str


class ShortlistExplanations(BaseModel):
    panel_summary: str
    # Keyed by candidate id so a partial response is still usable per candidate.
    candidates: dict[str, CandidateExplanation]


# ── rerank ────────────────────────────────────────────────────────────────
class RerankEntry(BaseModel):
    candidate_id: str
    relevance: float = Field(ge=0, le=100)
    rationale: str


class RerankResponse(BaseModel):
    rankings: list[RerankEntry]

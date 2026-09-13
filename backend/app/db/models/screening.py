"""Screening tables — one immutable run per matching attempt (architecture doc §13).

Re-running a job produces a new screening rather than editing an old one, so a
decision taken last month stays reconstructible with the scores and evidence it was
actually based on.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    desc,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.base import TimestampMixin, uuid_pk


class ScreeningStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ScreeningStage(enum.StrEnum):
    """The funnel stages, so a running screening reports where it is."""

    QUEUED = "queued"
    FILTERING = "filtering"
    RETRIEVING = "retrieving"
    RERANKING = "reranking"
    EVALUATING = "evaluating"
    SCORING = "scoring"
    EXPLAINING = "explaining"
    DONE = "done"


class Recommendation(enum.StrEnum):
    STRONG_HIRE = "strong_hire"
    INTERVIEW = "interview"
    MAYBE = "maybe"
    PASS = "pass"


class Screening(Base, TimestampMixin):
    __tablename__ = "screenings"

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(24), default=ScreeningStatus.QUEUED.value, nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(
        String(24), default=ScreeningStage.QUEUED.value, nullable=False
    )
    # "gemini" or "offline" — recorded per run so a result is never ambiguous about
    # which engine produced it.
    mode: Mapped[str] = mapped_column(String(16), default="offline", nullable=False)

    # Funnel telemetry: how wide each stage actually was on this run.
    pool_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    filtered_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retrieved_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reranked_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    evaluated_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    weights: Mapped[dict[str, float]] = mapped_column(JSONB, default=dict, nullable=False)
    panel_summary: Mapped[str | None] = mapped_column(Text)
    degradations: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()

    job: Mapped[Job] = relationship(back_populates="screenings")  # noqa: F821
    results: Mapped[list[ScreeningResult]] = relationship(
        back_populates="screening",
        cascade="all, delete-orphan",
        order_by="ScreeningResult.rank",
    )


class ScreeningResult(Base, TimestampMixin):
    __tablename__ = "screening_results"
    __table_args__ = (
        UniqueConstraint("screening_id", "candidate_id", name="uq_result_screening_candidate"),
        Index("ix_results_screening_rank", "screening_id", "rank"),
        # Reads that start from a person rather than a run — their best-fit jobs, the
        # best-match panel, the candidate list's best score. Neither index above can
        # serve a candidate_id-only predicate. Descending so the best score is first
        # and the ordering comes from the index.
        Index(
            "ix_results_candidate_score",
            "candidate_id",
            desc("composite_score"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    screening_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("screenings.id", ondelete="CASCADE"), nullable=False
    )
    # The single foreign key crossing the candidate/recruitment boundary.
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_profiles.id", ondelete="SET NULL")
    )

    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    composite_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    # { "required_skills": {"score": 82.0, "weight": 0.4, "contribution": 32.8}, ... }
    subscores: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    # Per-requirement verdicts with quoted evidence. The explanation generator reads
    # only this, never the resume text.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    matched_skills: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    missing_skills: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    retrieval_score: Mapped[float | None] = mapped_column(Numeric(6, 4))
    rerank_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    shortlisted: Mapped[bool] = mapped_column(default=False, nullable=False)
    recommendation: Mapped[str] = mapped_column(
        String(24), default=Recommendation.PASS.value, nullable=False
    )

    # Recruiter override (doc §19): AI output is a recommendation, not a decision.
    override_recommendation: Mapped[str | None] = mapped_column(String(24))
    override_note: Mapped[str | None] = mapped_column(Text)
    override_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    screening: Mapped[Screening] = relationship(back_populates="results")
    explanation: Mapped[Explanation | None] = relationship(
        back_populates="result", cascade="all, delete-orphan", uselist=False
    )


class Explanation(Base, TimestampMixin):
    __tablename__ = "explanations"

    id: Mapped[uuid.UUID] = uuid_pk()
    screening_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("screening_results.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    why_match: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    why_not: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    verdict: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(120), nullable=False)

    result: Mapped[ScreeningResult] = relationship(back_populates="explanation")

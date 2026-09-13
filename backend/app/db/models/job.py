"""Job-side tables (architecture doc §9)."""

from __future__ import annotations

import enum
import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.base import TimestampMixin, uuid_pk


class JobStatus(enum.StrEnum):
    OPEN = "open"
    #: Paused rather than finished — still visible, not accepting movement.
    ON_HOLD = "on_hold"
    CLOSED = "closed"


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), default=JobStatus.OPEN.value, nullable=False)

    # Recruiter-maintained descriptive fields. Not parsed from the description —
    # the parser guesses badly at org structure — so they stay null until set.
    department: Mapped[str | None] = mapped_column(String(120))
    employment_type: Mapped[str | None] = mapped_column(String(60))
    hiring_manager: Mapped[str | None] = mapped_column(String(160))

    requirement: Mapped[JobRequirement | None] = relationship(
        back_populates="job", cascade="all, delete-orphan", uselist=False
    )
    screenings: Mapped[list[Screening]] = relationship(  # noqa: F821
        back_populates="job", cascade="all, delete-orphan", order_by="Screening.created_at.desc()"
    )


class JobRequirement(Base, TimestampMixin):
    """Structured requirements parsed out of the description.

    ``required_skills`` carries an importance 1-5 per skill; the distinction between
    required and preferred is what the scoring engine's 40% category keys off, so it
    is stored explicitly rather than inferred at screening time.
    """

    __tablename__ = "job_requirements"

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    seniority: Mapped[str | None] = mapped_column(String(80))
    min_years_experience: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # [{ "skill": "PostgreSQL", "importance": 4 }, ...]
    required_skills: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    preferred_skills: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    domains: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    responsibilities: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    education: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    red_flags: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    # Hard gates applied in SQL before retrieval — see MatchingService stage 1.
    hard_filters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    parser_model: Mapped[str] = mapped_column(String(120), nullable=False)

    job: Mapped[Job] = relationship(back_populates="requirement")

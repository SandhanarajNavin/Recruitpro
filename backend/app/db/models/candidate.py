"""Candidate identity and the structured profile derived from a resume.

The persistent repository (architecture doc §8). Profiles are versioned: re-ingesting
adds a version rather than overwriting, so a past screening stays reproducible.
"""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.base import TimestampMixin, uuid_pk

if TYPE_CHECKING:  # avoids a circular import; SQLAlchemy resolves the string form
    from app.db.models.resume import Resume


class CandidateStatus(enum.StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class Candidate(Base, TimestampMixin):
    __tablename__ = "candidates"
    __table_args__ = (Index("ix_candidates_owner_status", "owner_id", "status"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(64))
    location: Mapped[str | None] = mapped_column(String(160))
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(24), default=CandidateStatus.ACTIVE.value, nullable=False
    )

    # Recruiter-maintained, not parsed. A resume rarely states notice period or
    # salary expectation reliably, and inventing them from context would be worse
    # than leaving them blank.
    linkedin_url: Mapped[str | None] = mapped_column(String(320))
    availability_days: Mapped[int | None] = mapped_column(Integer)
    expected_salary: Mapped[str | None] = mapped_column(String(80))
    #: Recruiter's own notes. Kept apart from parser output so a reprocess
    #: never overwrites what a person wrote.
    notes: Mapped[str | None] = mapped_column(Text)

    resumes: Mapped[list[Resume]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", order_by="Resume.version.desc()"
    )
    profiles: Mapped[list[CandidateProfile]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        order_by="CandidateProfile.version.desc()",
    )

    @property
    def latest_profile(self) -> CandidateProfile | None:
        return self.profiles[0] if self.profiles else None


class CandidateProfile(Base, TimestampMixin):
    """Structured intelligence derived from one resume version.

    JSONB rather than fully normalised child tables: the shape is read whole, written
    once per ingestion, and searched by containment — which JSONB with a GIN index
    does well. Normalising skills into their own table is a later move, once
    skill-level analytics actually need it.
    """

    __tablename__ = "candidate_profiles"
    __table_args__ = (Index("ix_profiles_candidate_version", "candidate_id", "version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False
    )
    current_title: Mapped[str | None] = mapped_column(String(200))
    total_years_experience: Mapped[float] = mapped_column(Numeric(4, 1), default=0, nullable=False)
    seniority_rank: Mapped[int] = mapped_column(Integer, default=3, nullable=False)

    # Multi-label role classification (architecture doc §8). The primary role drives
    # the role scoring factor; secondary roles widen retrieval without diluting it.
    primary_role: Mapped[str | None] = mapped_column(String(120), index=True)
    secondary_roles: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    role_confidence: Mapped[float | None] = mapped_column(Numeric(3, 2))
    classifier_model: Mapped[str | None] = mapped_column(String(120))

    skills: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    domains: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    experience: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    education: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    certifications: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    projects: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    achievements: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    # Provenance: which parser produced this, so a re-parse is comparable.
    parser_model: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    candidate: Mapped[Candidate] = relationship(back_populates="profiles")

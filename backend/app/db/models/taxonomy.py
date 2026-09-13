"""Canonical skill and industry dictionaries (architecture doc §7.2).

Skills and industries arrive from resumes and job descriptions as free text —
"ReactJS", "React.js" and "React" are one skill. Normalising them into a shared
dictionary is what makes a skill filter mean the same thing for every recruiter, and
what lets ``candidate_skills`` carry a proficiency the JSONB array never could.

The dictionaries are global rather than per-recruiter on purpose: a canonical skill
name is not tenant data, and duplicating it per recruiter would make cross-tenant
skill statistics impossible to compute later without a migration.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.base import TimestampMixin, uuid_pk


class Proficiency(enum.StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class Skill(Base, TimestampMixin):
    """One canonical skill. ``aliases`` holds the surface forms that map onto it."""

    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = uuid_pk()
    canonical_name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    #: Lowercased match key, so lookup never depends on display casing.
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    aliases: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    category: Mapped[str | None] = mapped_column(String(60))


class Industry(Base, TimestampMixin):
    __tablename__ = "industries"

    id: Mapped[uuid.UUID] = uuid_pk()
    canonical_name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    aliases: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)


class CandidateSkill(Base, TimestampMixin):
    """Normalised candidate ↔ skill edge, carrying proficiency and provenance."""

    __tablename__ = "candidate_skills"
    __table_args__ = (
        UniqueConstraint("candidate_id", "skill_id", name="uq_candidate_skill"),
        Index("ix_candidate_skills_skill", "skill_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skills.id", ondelete="CASCADE"), nullable=False
    )
    proficiency: Mapped[str | None] = mapped_column(String(24))
    #: Years of stated use, when the resume supports inferring it.
    years: Mapped[float | None] = mapped_column(Numeric(4, 1))
    #: The surface form this edge was created from, kept for auditability.
    source_text: Mapped[str | None] = mapped_column(String(160))

    skill: Mapped[Skill] = relationship(lazy="joined")


class CandidateIndustry(Base, TimestampMixin):
    __tablename__ = "candidate_industries"
    __table_args__ = (
        UniqueConstraint("candidate_id", "industry_id", name="uq_candidate_industry"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    industry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("industries.id", ondelete="CASCADE"), nullable=False
    )

    industry: Mapped[Industry] = relationship(lazy="joined")


class JobSkill(Base, TimestampMixin):
    """JD ↔ skill edge. ``required`` separates hard gates from nice-to-haves."""

    __tablename__ = "job_skills"
    __table_args__ = (UniqueConstraint("job_id", "skill_id", name="uq_job_skill"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skills.id", ondelete="CASCADE"), nullable=False
    )
    required: Mapped[bool] = mapped_column(default=True, nullable=False)
    #: 1-5 importance, mirroring ``RequiredSkill.importance`` from the AI schema.
    weight: Mapped[int] = mapped_column(default=3, nullable=False)

    skill: Mapped[Skill] = relationship(lazy="joined")

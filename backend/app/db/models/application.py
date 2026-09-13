"""Applications: a candidate's progress through the hiring process for one job.

This is the piece that turns a matching engine into a hiring tool. Screening ranks
candidates against a job and never mutates them; an application is the mutable
record of what a recruiter then *did* about that ranking.

The two stay separate on purpose. A screening is an immutable snapshot — re-running
it produces a new one — while an application moves forward over weeks and must
survive every re-screen. Linking them by ``origin_screening_result_id`` records
where an application came from without letting the screening own its lifecycle.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.base import TimestampMixin, uuid_pk


class ApplicationStage(enum.StrEnum):
    APPLIED = "applied"
    SCREENING = "screening"
    SHORTLISTED = "shortlisted"
    INTERVIEW = "interview"
    OFFER = "offer"
    HIRED = "hired"
    REJECTED = "rejected"


#: Stages a candidate is still in play at. Anything else is an outcome.
ACTIVE_STAGES = (
    ApplicationStage.APPLIED,
    ApplicationStage.SCREENING,
    ApplicationStage.SHORTLISTED,
    ApplicationStage.INTERVIEW,
    ApplicationStage.OFFER,
)

#: Ordered for the pipeline display. Rejected sits outside the funnel rather than
#: at the end of it — it is an exit, not a further step.
PIPELINE_ORDER = (
    ApplicationStage.APPLIED,
    ApplicationStage.SCREENING,
    ApplicationStage.SHORTLISTED,
    ApplicationStage.INTERVIEW,
    ApplicationStage.OFFER,
    ApplicationStage.HIRED,
)


class InterviewOutcome(enum.StrEnum):
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class Application(Base, TimestampMixin):
    """One candidate against one job. At most one per pair."""

    __tablename__ = "applications"
    __table_args__ = (
        # A candidate applies to a job once. Re-screening must not create a second.
        UniqueConstraint("candidate_id", "job_id", name="uq_application_candidate_job"),
        Index("ix_applications_job_stage", "job_id", "stage"),
        Index("ix_applications_candidate", "candidate_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(
        String(24), default=ApplicationStage.APPLIED.value, nullable=False, index=True
    )

    #: Which screening surfaced this candidate, when one did. SET NULL rather than
    #: CASCADE: deleting a screening must not delete a hire that came from it.
    origin_screening_result_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("screening_results.id", ondelete="SET NULL")
    )
    #: Copied from the screening at the moment of creation. The screening can be
    #: re-run and produce a different number; what the recruiter acted on should not
    #: change retroactively.
    match_score_at_entry: Mapped[float | None] = mapped_column(Numeric(5, 2))

    rejection_reason: Mapped[str | None] = mapped_column(Text)
    #: Set when the application reaches a terminal stage, for time-to-hire reporting.
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    events: Mapped[list[ApplicationEvent]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="ApplicationEvent.created_at",
    )
    interviews: Mapped[list[Interview]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="Interview.scheduled_at",
    )


class ApplicationEvent(Base, TimestampMixin):
    """Every stage change, kept forever.

    "When did this candidate reach interview, and who moved them" is the question a
    pipeline report is built from, and it cannot be reconstructed from the current
    stage alone.
    """

    __tablename__ = "application_events"
    __table_args__ = (Index("ix_app_events_application", "application_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    #: Null on the first event — there was no previous stage.
    from_stage: Mapped[str | None] = mapped_column(String(24))
    to_stage: Mapped[str] = mapped_column(String(24), nullable=False)
    #: Nullable so an automated transition is distinguishable from a person's.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)

    application: Mapped[Application] = relationship(back_populates="events")


class Interview(Base, TimestampMixin):
    __tablename__ = "interviews"
    __table_args__ = (Index("ix_interviews_scheduled", "scheduled_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Free text — "Technical", "Culture fit". An enum here would be guessing at a
    #: taxonomy every company defines differently.
    kind: Mapped[str | None] = mapped_column(String(60))
    interviewer: Mapped[str | None] = mapped_column(String(160))
    outcome: Mapped[str] = mapped_column(
        String(24), default=InterviewOutcome.SCHEDULED.value, nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)

    application: Mapped[Application] = relationship(back_populates="interviews")

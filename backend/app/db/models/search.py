"""Search sessions and their result pages (architecture doc §13).

"Show me other profiles" is answered from persisted state, never from model memory.
A session pins one ranked list; each page marks the rows it returned as ``shown``,
and the next page excludes them. That makes pagination reproducible, auditable, and
independent of whether an LLM is in the loop at all.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.base import TimestampMixin, uuid_pk


class SearchSessionStatus(enum.StrEnum):
    ACTIVE = "active"
    EXHAUSTED = "exhausted"


class SearchSession(Base, TimestampMixin):
    __tablename__ = "search_sessions"
    __table_args__ = (Index("ix_search_sessions_owner_job", "recruiter_id", "job_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    #: The authenticated recruiter. Named per doc §6 — never taken from the client.
    recruiter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    #: The screening run that produced this ranking, when one exists.
    screening_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("screenings.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(24), default=SearchSessionStatus.ACTIVE.value, nullable=False
    )
    page_size: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    #: Filters actually applied, so a broadened search can say what it relaxed.
    applied_filters: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    relaxations: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    results: Mapped[list[SearchResult]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="SearchResult.rank",
    )


class SearchResult(Base, TimestampMixin):
    """One candidate's position in a session's ranking.

    ``shown`` is what makes "next page" work without re-running the funnel: the rows
    exist from the moment the session is created, and paging only flips this flag.
    """

    __tablename__ = "search_results"
    __table_args__ = (
        Index("ix_search_results_session_rank", "session_id", "rank"),
        Index("ix_search_results_session_shown", "session_id", "shown"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("search_sessions.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    #: Denormalised from the screening result so a session survives independently.
    screening_result_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("screening_results.id", ondelete="SET NULL")
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    shown: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    shown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped[SearchSession] = relationship(back_populates="results")

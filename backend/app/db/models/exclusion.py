"""Candidates a recruiter has taken out of one job's rankings.

Distinct from a rejection on purpose. A rejection is a decision about a person for
a role and stays visible — the recruiter wants to see "we already said no to this
one" when the name comes back around. An exclusion is about the *list*: it says
"stop showing me this row for this job", and it is scoped to the job so a candidate
removed from one search still competes for every other.

Kept in its own table rather than as a flag on ``applications`` because a candidate
can be excluded from a job they never applied to, and a flag there would force a
fake application row into existence to hold it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.models.base import TimestampMixin, uuid_pk


class JobCandidateExclusion(Base, TimestampMixin):
    __tablename__ = "job_candidate_exclusions"
    __table_args__ = (
        # One exclusion per pair; re-hiding an already-hidden candidate is a no-op
        # rather than a second row.
        UniqueConstraint("job_id", "candidate_id", name="uq_exclusion_job_candidate"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Who hid them, for the audit trail. Nullable so deleting a user does not
    #: silently un-hide candidates.
    excluded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

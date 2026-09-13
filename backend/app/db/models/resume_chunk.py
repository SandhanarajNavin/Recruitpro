"""Retrievable passages of a resume.

The candidate-level vector is built from *parsed* fields — title, skills, domains —
which is what ranking a whole person against a whole job needs. It cannot answer
"what did this person actually do at Globex", because the sentence that says so was
never embedded.

These rows are that missing layer: the resume's own words, split into passages, one
vector each. Kept separate from ``candidate_profiles`` because they are derived from
a single resume version and are replaced wholesale when it is re-ingested, and
separate from ``embeddings`` because the text has to come back with the hit — a
passage the recruiter cannot read is not an answer.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.models.base import TimestampMixin, uuid_pk


class ResumeChunk(Base, TimestampMixin):
    __tablename__ = "resume_chunks"
    __table_args__ = (
        UniqueConstraint("resume_id", "ordinal", name="uq_chunk_resume_ordinal"),
        # Passage search filters to one candidate often enough to index it.
        Index("ix_resume_chunks_candidate", "candidate_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Denormalised from the resume so a passage hit can be scoped by owner and
    #: attributed to a person without a second join at query time.
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    #: Position in the resume, so retrieved passages can be shown in document order.
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

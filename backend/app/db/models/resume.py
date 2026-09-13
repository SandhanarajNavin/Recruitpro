"""Resume files and their ingestion state.

Retained per version rather than overwritten, so a screening decision stays auditable
against the document it was made from, and re-processing is possible when the parsing
or embedding model changes.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.base import TimestampMixin, uuid_pk

if TYPE_CHECKING:  # avoids a circular import; SQLAlchemy resolves the string form
    from app.db.models.candidate import Candidate


class ResumeStatus(enum.StrEnum):
    """Mirrors the ingestion pipeline stages so the UI can show real progress."""

    QUEUED = "queued"
    EXTRACTING = "extracting"
    PARSING = "parsing"
    EMBEDDING = "embedding"
    READY = "ready"
    FAILED = "failed"


class Resume(Base, TimestampMixin):
    __tablename__ = "resumes"
    __table_args__ = (Index("ix_resumes_candidate_version", "candidate_id", "version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    # Key into object storage. The file itself never leaves the private bucket;
    # the API mints a short-lived signed URL after an authorization check.
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(160), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(64), index=True)
    extracted_text: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default=ResumeStatus.QUEUED.value, nullable=False, index=True
    )
    error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column()

    candidate: Mapped[Candidate] = relationship(back_populates="resumes")

"""Persisted audit trail (architecture doc §7.2, §19).

Structured logs answer "what happened recently"; this table answers "who accessed
this candidate, and when" months later. Denied access is recorded as deliberately as
granted access — an attempted cross-tenant read is the event most worth keeping.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.models.base import TimestampMixin, uuid_pk


class AuditAction(enum.StrEnum):
    RESUME_UPLOADED = "resume.uploaded"
    RESUME_VIEWED = "resume.viewed"
    RESUME_DOWNLOADED = "resume.downloaded"
    RESUME_DELETED = "resume.deleted"
    CANDIDATE_VIEWED = "candidate.viewed"
    SEARCH_RUN = "search.run"
    SEARCH_PAGED = "search.paged"
    ACCESS_DENIED = "access.denied"


class AuditEvent(Base, TimestampMixin):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_recruiter_created", "recruiter_id", "created_at"),
        Index("ix_audit_resource", "resource_type", "resource_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    #: Nullable: a denied request may not resolve to a recruiter at all.
    recruiter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(48))
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    #: Correlates with the request_id / processing_id in the structured logs (§20).
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    #: Never PII — counts, ids and outcomes only.
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

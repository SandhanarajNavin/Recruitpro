from __future__ import annotations

import enum
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.db.database import Base
from app.db.models.base import TimestampMixin, uuid_pk


class EmbeddingOwner(enum.StrEnum):
    """One table serves both vector kinds, discriminated by owner_type.

    This is what makes an embedding-model upgrade a backfill rather than a schema
    migration: model and dim travel with the row, and retrieval only ever compares
    vectors produced by the same model.
    """

    CANDIDATE = "candidate"
    JOB = "job"
    #: One passage of a resume. Many per candidate, unlike the two above.
    RESUME_CHUNK = "resume_chunk"


class Embedding(Base, TimestampMixin):
    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", "model", name="uq_embedding_owner_model"),
        Index("ix_embeddings_owner", "owner_type", "owner_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_type: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    vector: Mapped[list[float]] = mapped_column(Vector(settings.embedding_dim), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

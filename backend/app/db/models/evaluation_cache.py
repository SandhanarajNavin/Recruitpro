"""Cached requirement evaluations.

Model settings can reduce run-to-run variance but cannot remove it: temperature 0
and a fixed seed are best-effort on a hosted model, not a guarantee. Measured on this
codebase, one candidate in nine still moved between two identical screenings.

So the system stops asking. An evaluation is a pure function of (job requirements,
candidate profile, resume text, model, prompt version); when all of those are
unchanged, the stored answer is reused instead of paying for a fresh one. That makes
a re-run *exactly* reproducible rather than nearly so, and re-screening a settled
pool costs nothing.

The fingerprint is deliberately strict. Anything that could change the answer is in
it — including ``prompt_version``, so editing the evaluator's instructions
invalidates every cached row rather than silently mixing two prompts' output in one
shortlist.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.models.base import TimestampMixin, uuid_pk


class EvaluationCache(Base, TimestampMixin):
    __tablename__ = "evaluation_cache"
    __table_args__ = (Index("ix_eval_cache_model", "model"),)

    id: Mapped[uuid.UUID] = uuid_pk()

    #: sha256 over the canonical inputs. Unique, so a concurrent double-evaluation
    #: collapses to one row instead of racing.
    fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )

    #: Kept alongside the hash for debugging — a cache you cannot inspect is a
    #: cache you cannot trust when a score looks wrong.
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)

    #: The serialised CandidateEvaluation.
    evaluation: Mapped[dict] = mapped_column(JSONB, nullable=False)

    #: Cheap usage signal: how often this row saved a model call.
    hits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

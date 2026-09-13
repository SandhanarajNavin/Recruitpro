"""Job queries. Scoped by owner on every read — a recruiter never sees another's roles."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Job


def get(session: Session, job_id: uuid.UUID, owner_id: uuid.UUID) -> Job | None:
    return session.execute(
        select(Job).where(Job.id == job_id, Job.owner_id == owner_id)
    ).scalars().first()


def list_for_owner(session: Session, owner_id: uuid.UUID, limit: int = 50) -> list[Job]:
    return list(
        session.execute(
            select(Job)
            .where(Job.owner_id == owner_id)
            .order_by(Job.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )

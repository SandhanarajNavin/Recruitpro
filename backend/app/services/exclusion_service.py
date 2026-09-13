"""Candidates a recruiter has removed from one job's rankings.

The funnel enforces these as a SQL gate (see ``candidate_repository._EXCLUSION_GATE``),
so a re-run never brings a removed candidate back. This module is the write side and
the read side for already-stored results, which are not re-run and so have to be
filtered when they are served.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JobCandidateExclusion


def exclude(
    session: Session,
    *,
    job_id: uuid.UUID,
    candidate_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> JobCandidateExclusion:
    """Hide a candidate from one job's rankings. Idempotent."""
    existing = session.execute(
        select(JobCandidateExclusion).where(
            JobCandidateExclusion.job_id == job_id,
            JobCandidateExclusion.candidate_id == candidate_id,
        )
    ).scalars().first()
    if existing is not None:
        return existing

    row = JobCandidateExclusion(
        job_id=job_id, candidate_id=candidate_id, excluded_by=actor_id
    )
    session.add(row)
    session.commit()
    return row


def restore(session: Session, *, job_id: uuid.UUID, candidate_id: uuid.UUID) -> bool:
    """Put a removed candidate back. Returns whether anything was removed."""
    existing = session.execute(
        select(JobCandidateExclusion).where(
            JobCandidateExclusion.job_id == job_id,
            JobCandidateExclusion.candidate_id == candidate_id,
        )
    ).scalars().first()
    if existing is None:
        return False
    session.delete(existing)
    session.commit()
    return True


def excluded_candidate_ids(session: Session, job_id: uuid.UUID) -> set[uuid.UUID]:
    """Everyone hidden from this job, for filtering results already on disk."""
    return set(
        session.execute(
            select(JobCandidateExclusion.candidate_id).where(
                JobCandidateExclusion.job_id == job_id
            )
        )
        .scalars()
        .all()
    )

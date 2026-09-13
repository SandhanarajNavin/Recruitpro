"""Resume queries used by ingestion.

Version lookups live here rather than in the service because both the resume and the
profile tables advance versions independently, and the "next version" rule is a
property of the table, not of the pipeline step that happens to need it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Candidate, CandidateProfile, Resume


def find_by_checksum(session: Session, checksum: str, owner_id: uuid.UUID) -> Resume | None:
    """Same bytes uploaded twice is a no-op rather than a duplicate candidate."""
    return session.execute(
        select(Resume)
        .join(Candidate)
        .where(Resume.checksum == checksum, Candidate.owner_id == owner_id)
    ).scalars().first()


def find_candidate_by_email(
    session: Session, owner_id: uuid.UUID, email: str, exclude_id: uuid.UUID
) -> Candidate | None:
    return session.execute(
        select(Candidate).where(
            Candidate.owner_id == owner_id,
            Candidate.email == email,
            Candidate.id != exclude_id,
        )
    ).scalars().first()


def next_resume_version(session: Session, candidate_id: uuid.UUID) -> int:
    current = (
        session.execute(
            select(Resume.version)
            .where(Resume.candidate_id == candidate_id)
            .order_by(Resume.version.desc())
        )
        .scalars()
        .first()
    )
    return (current or 0) + 1


def next_profile_version(session: Session, candidate_id: uuid.UUID) -> int:
    current = (
        session.execute(
            select(CandidateProfile.version)
            .where(CandidateProfile.candidate_id == candidate_id)
            .order_by(CandidateProfile.version.desc())
        )
        .scalars()
        .first()
    )
    return (current or 0) + 1

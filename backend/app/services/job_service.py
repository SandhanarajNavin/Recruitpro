"""Job creation and JD parsing (architecture doc §9).

A recruiter can open a role without uploading anything — the repository already
holds the candidates.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.ai import skill_normalizer
from app.ai.embeddings import get_embedder
from app.core.logging import audit, get_logger
from app.db.models import Embedding, Job, JobRequirement
from app.db.models.embedding import EmbeddingOwner
from app.db.models.job import JobStatus
from app.db.repositories import job_repository
from app.parsers.jd_parser import hard_filters_from, job_embedding_text, parse_job_description

logger = get_logger(__name__)


def create_job(
    session: Session,
    *,
    owner_id: uuid.UUID,
    title: str | None,
    description: str,
    location: str | None = None,
) -> Job:
    """Creates the job, parses requirements, and stores the JD vector.

    Parsing happens inline rather than on a worker: a recruiter needs to see and
    correct the extracted requirements immediately, and one JD parse is a single
    short call, not a fan-out.
    """
    parsed, parser_model = parse_job_description(description)

    job = Job(
        owner_id=owner_id,
        title=(title or parsed.title)[:200],
        description=description,
        location=location,
    )
    session.add(job)
    session.flush()

    requirement = JobRequirement(
        job_id=job.id,
        seniority=parsed.seniority[:80] if parsed.seniority else None,
        min_years_experience=parsed.min_years_experience,
        required_skills=[entry.model_dump() for entry in parsed.required_skills],
        preferred_skills=parsed.preferred_skills,
        domains=parsed.domains,
        responsibilities=parsed.responsibilities,
        education=parsed.education,
        red_flags=parsed.red_flags,
        hard_filters=hard_filters_from(parsed),
        parser_model=parser_model,
    )
    session.add(requirement)

    # Canonical JD skill edges, so a job can be filtered and joined on the same
    # dictionary rows the candidates use (doc §7.2).
    skill_normalizer.sync_job_skills(
        session,
        job.id,
        required=[(entry.skill, entry.importance) for entry in parsed.required_skills],
        preferred=parsed.preferred_skills,
    )

    embedder = get_embedder()
    session.add(
        Embedding(
            owner_type=EmbeddingOwner.JOB.value,
            owner_id=job.id,
            vector=embedder.embed(job_embedding_text(parsed)),
            model=embedder.model,
            dim=embedder.dim,
        )
    )

    session.commit()
    audit(
        "job.created",
        job_id=job.id,
        owner_id=owner_id,
        parser=parser_model,
        required_skills=len(parsed.required_skills),
    )
    return job


def get_job(session: Session, job_id: uuid.UUID, owner_id: uuid.UUID) -> Job | None:
    return job_repository.get(session, job_id, owner_id)


def list_jobs(session: Session, owner_id: uuid.UUID, limit: int = 50) -> list[Job]:
    return job_repository.list_for_owner(session, owner_id, limit)


#: Fields a recruiter may change after a job is created. The description is not
#: among them: it is what the requirements were parsed from, and editing it would
#: leave the parsed requirements describing a job that no longer exists.
EDITABLE_FIELDS = ("title", "location", "department", "employment_type", "hiring_manager")


class JobError(ValueError):
    """A job update that the domain refuses."""


def update_job(
    session: Session,
    job_id: uuid.UUID,
    owner_id: uuid.UUID,
    *,
    status: str | None = None,
    **fields: str | None,
) -> Job:
    """Apply an edit to a job the caller owns.

    Ownership is a query filter rather than a check after the fact, so another
    recruiter's job is indistinguishable from one that does not exist.
    """
    job = job_repository.get(session, job_id, owner_id)
    if job is None:
        raise JobError("Job not found.")

    if status is not None:
        valid = {member.value for member in JobStatus}
        if status not in valid:
            raise JobError(f"Unknown status {status!r}. Expected one of {sorted(valid)}.")
        job.status = status

    for name, value in fields.items():
        if name not in EDITABLE_FIELDS:
            raise JobError(f"{name} cannot be edited.")
        if value is not None:
            # Empty string clears an optional field; None means "leave alone".
            setattr(job, name, value.strip() or None)

    session.commit()
    session.refresh(job)
    return job

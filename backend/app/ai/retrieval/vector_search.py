"""Semantic retrieval — funnel stage 2.

Embeds the job description and hands the vector to the candidate repository, which
runs the hard filters and the ANN scan as one statement. The SQL stays in the
repository on purpose: the filter has to run where the data lives, and splitting the
gate from the ordering would mean over-fetching and filtering in Python.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.ai.embeddings import get_embedder
from app.ai.schemas import ParsedJobDescription
from app.db.repositories.candidate_repository import RetrievedCandidate, retrieve
from app.parsers.jd_parser import job_embedding_text

__all__ = ["RetrievedCandidate", "search"]


def search(
    session: Session,
    *,
    owner_id: uuid.UUID,
    job: ParsedJobDescription,
    must_have_skills: list[str],
    min_years: int,
    limit: int,
    job_id: uuid.UUID | None = None,
) -> list[RetrievedCandidate]:
    """Nearest candidates to this job, already past the hard gates.

    ``job_id`` scopes the per-job exclusions — candidates the recruiter removed from
    this job's rankings. Optional so a caller with no job row still works.
    """
    embedder = get_embedder()
    job_vector = embedder.embed(job_embedding_text(job))
    return retrieve(
        session,
        owner_id,
        job_vector=job_vector,
        embedding_model=embedder.model,
        must_have_skills=must_have_skills,
        min_years=min_years,
        limit=limit,
        job_id=job_id,
    )

"""Resume ingestion (architecture doc §7).

Two halves, split by the HTTP boundary:

- ``accept_upload``  runs inside the request: validate, store, insert a row, enqueue.
- ``process_resume`` runs on a worker: extract, parse, upsert candidate, embed.

No job description is in scope anywhere in this module. That is the constraint the
whole architecture rests on.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import classifier, skill_normalizer
from app.ai.embeddings import get_embedder
from app.ai.lexicon import seniority_rank
from app.core.logging import audit, get_logger
from app.db.models import Candidate, CandidateProfile, Embedding, Resume, ResumeStatus
from app.db.models.embedding import EmbeddingOwner
from app.db.repositories import resume_repository
from app.parsers import ExtractionError, extract_text
from app.ai.retrieval import passage_search
from app.parsers.resume_parser import parse_resume, profile_text_for_embedding
from app.storage import get_storage
from app.utils.file_utils import (
    UploadRejected,
    provisional_name,
    sha256_checksum,
    validate_upload,
)

logger = get_logger(__name__)

#: Re-exported: the API layer raises/catches these from the ingestion entrypoint.
__all__ = ["UploadRejected", "accept_upload", "process_resume", "validate_upload"]


def accept_upload(
    session: Session,
    *,
    owner_id: uuid.UUID,
    filename: str,
    content_type: str,
    data: bytes,
) -> Resume:
    """Synchronous half of ingestion. Returns immediately after enqueueing."""
    validate_upload(filename, content_type, data)

    storage = get_storage()
    checksum = sha256_checksum(data)

    # Same bytes uploaded twice is a no-op rather than a duplicate candidate.
    existing = resume_repository.find_by_checksum(session, checksum, owner_id)
    if existing is not None:
        logger.info("Duplicate upload ignored (checksum match): %s", filename)
        return existing

    # A provisional candidate: the real name and email arrive from the parser, and
    # `_resolve_identity` merges this record into an existing candidate if the parsed
    # email already belongs to one.
    candidate = Candidate(owner_id=owner_id, full_name=provisional_name(filename))
    session.add(candidate)
    session.flush()

    key = f"{owner_id}/{candidate.id}/v1-{filename}"
    stored = storage.put(key, data, content_type or "application/octet-stream")

    resume = Resume(
        candidate_id=candidate.id,
        storage_key=stored.key,
        original_filename=filename,
        content_type=content_type or "application/octet-stream",
        size_bytes=stored.size_bytes,
        checksum=stored.checksum,
        version=1,
        status=ResumeStatus.QUEUED.value,
    )
    session.add(resume)
    session.commit()

    audit("resume.uploaded", resume_id=resume.id, candidate_id=candidate.id, owner_id=owner_id)
    return resume


def _resolve_identity(
    session: Session, resume: Resume, candidate: Candidate, parsed
) -> Candidate:
    """Basic identity resolution: an email that already exists means one person.

    Deliberately limited to an exact email match. Fuzzy name matching produces false
    merges, and merging two real people's histories is far worse than holding a
    duplicate — full dedup is listed as later work in the architecture doc §22.
    """
    if parsed.email:
        twin = resume_repository.find_candidate_by_email(
            session, candidate.owner_id, parsed.email, candidate.id
        )

        if twin is not None:
            next_version = resume_repository.next_resume_version(session, twin.id)
            logger.info(
                "Merging provisional candidate %s into %s on email match", candidate.id, twin.id
            )
            # Re-parent through the relationship, not just the FK column.
            # ``Candidate.resumes`` cascades "all, delete-orphan", so if the resume
            # is still in the provisional candidate's collection when that candidate
            # is deleted, the ORM deletes the resume too — and the profile insert
            # that follows then fails with a foreign-key violation, losing the whole
            # upload. Moving it between collections keeps the ORM's view in step
            # with the column.
            candidate.resumes.remove(resume)
            twin.resumes.append(resume)
            resume.version = next_version
            session.delete(candidate)
            session.flush()
            audit(
                "candidate.merged",
                merged_from=candidate.id,
                merged_into=twin.id,
                resume_id=resume.id,
            )
            return twin

    if parsed.full_name:
        candidate.full_name = parsed.full_name[:200]
    candidate.email = parsed.email
    candidate.phone = parsed.phone
    candidate.location = parsed.location
    candidate.summary = parsed.summary
    return candidate


def _upsert_embedding(
    session: Session, candidate_id: uuid.UUID, vector: list[float], model: str, dim: int
) -> None:
    """One vector per candidate per model. Re-ingesting replaces the vector in place
    so retrieval never sees two competing vectors for one person."""
    existing = session.execute(
        select(Embedding).where(
            Embedding.owner_type == EmbeddingOwner.CANDIDATE.value,
            Embedding.owner_id == candidate_id,
            Embedding.model == model,
        )
    ).scalars().first()

    if existing is not None:
        existing.vector = vector
        existing.dim = dim
        existing.version += 1
        return

    session.add(
        Embedding(
            owner_type=EmbeddingOwner.CANDIDATE.value,
            owner_id=candidate_id,
            vector=vector,
            model=model,
            dim=dim,
        )
    )


def process_resume(session: Session, resume_id: uuid.UUID) -> None:
    """Asynchronous half of ingestion. Idempotent: safe to retry after any failure."""
    resume = session.get(Resume, resume_id)
    if resume is None:
        logger.warning("process_resume called for unknown resume %s", resume_id)
        return

    candidate = session.get(Candidate, resume.candidate_id)
    if candidate is None:
        logger.warning("Resume %s has no candidate; skipping.", resume_id)
        return

    try:
        # ── extract ───────────────────────────────────────────────────────
        resume.status = ResumeStatus.EXTRACTING.value
        resume.error = None
        session.commit()

        data = get_storage().get(resume.storage_key)
        text = extract_text(data, resume.content_type, resume.original_filename)
        resume.extracted_text = text
        session.commit()

        # ── parse ─────────────────────────────────────────────────────────
        resume.status = ResumeStatus.PARSING.value
        session.commit()

        parsed, parser_model = parse_resume(text)
        candidate = _resolve_identity(session, resume, candidate, parsed)

        next_profile_version = resume_repository.next_profile_version(session, candidate.id)

        # ── classify + normalise (doc §8) ─────────────────────────────
        classification = classifier.classify(
            current_title=parsed.current_title,
            skills=parsed.skills,
            experience_titles=[entry.title for entry in parsed.experience],
        )

        profile = CandidateProfile(
            candidate_id=candidate.id,
            resume_id=resume.id,
            current_title=(parsed.current_title or None),
            total_years_experience=float(parsed.total_years_experience),
            seniority_rank=seniority_rank(parsed.current_title or ""),
            primary_role=classification.primary_role,
            secondary_roles=classification.secondary_roles,
            role_confidence=classification.confidence,
            classifier_model=classification.engine,
            skills=parsed.skills,
            domains=parsed.domains,
            experience=[entry.model_dump() for entry in parsed.experience],
            education=parsed.education,
            certifications=parsed.certifications,
            projects=parsed.projects,
            achievements=parsed.achievements,
            parser_model=parser_model,
            version=next_profile_version,
        )
        session.add(profile)

        # Canonical skill/industry edges, replaced wholesale so a re-parse never
        # accumulates duplicates.
        skill_count = skill_normalizer.sync_candidate_skills(
            session, candidate.id, parsed.skills
        )
        industry_count = skill_normalizer.sync_candidate_industries(
            session, candidate.id, parsed.domains
        )
        session.commit()

        # ── embed ─────────────────────────────────────────────────────────
        resume.status = ResumeStatus.EMBEDDING.value
        session.commit()

        embedder = get_embedder()
        vector = embedder.embed(profile_text_for_embedding(parsed))
        _upsert_embedding(session, candidate.id, vector, embedder.model, embedder.dim)

        # Passage index, for questions about what the resume actually says. Separate
        # from the vector above: that one is built from parsed fields and ranks the
        # whole person, this one is the resume's own words. Failure here must not
        # fail the ingestion — the candidate is still screenable without passages.
        try:
            passages = passage_search.index_resume(session, resume, candidate.id)
        except Exception:
            logger.exception("Passage indexing failed for resume %s", resume.id)
            session.rollback()
            passages = 0

        resume.status = ResumeStatus.READY.value
        resume.processed_at = datetime.now(UTC)
        session.commit()

        audit(
            "resume.processed",
            resume_id=resume.id,
            candidate_id=candidate.id,
            parser=parser_model,
            embedding_model=embedder.model,
            passages=passages,
            skills=len(parsed.skills),
            normalised_skills=skill_count,
            industries=industry_count,
            primary_role=classification.primary_role,
        )

    except ExtractionError as exc:
        session.rollback()
        _fail(session, resume_id, str(exc))
    except Exception as exc:  # noqa: BLE001 - a failed resume must not kill the worker
        session.rollback()
        logger.exception("Ingestion failed for resume %s", resume_id)
        _fail(session, resume_id, f"Ingestion failed: {exc}")


def _fail(session: Session, resume_id: uuid.UUID, message: str) -> None:
    resume = session.get(Resume, resume_id)
    if resume is None:
        return
    resume.status = ResumeStatus.FAILED.value
    resume.error = message[:2000]
    session.commit()
    audit("resume.failed", resume_id=resume_id, error=message[:500])

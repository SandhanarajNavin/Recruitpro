"""Read-through cache for requirement evaluations.

Turns "the model is nearly deterministic" into "the system is exactly deterministic":
identical inputs return the identical stored evaluation, so a re-run of an unchanged
screening reproduces byte for byte.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.schemas import (
    CandidateEvaluation,
    ParsedJobDescription,
    ParsedResume,
    RerankResponse,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.evaluation_cache import EvaluationCache

logger = get_logger(__name__)

#: Bump when the evaluator's prompt, categories or scoring rules change. Every
#: cached row becomes unreachable, which is the point — mixing two prompts' output
#: inside one shortlist would make the ranking incoherent.
PROMPT_VERSION = 1


def fingerprint(
    job: ParsedJobDescription, profile: ParsedResume, resume_text: str
) -> str:
    """Stable hash of everything that can change the evaluation.

    ``sort_keys`` and an explicit field list rather than ``model_dump_json``: a
    Pydantic field reordering would otherwise silently invalidate the whole cache.
    """
    payload = {
        "job": {
            "title": job.title,
            "seniority": job.seniority,
            "min_years": job.min_years_experience,
            "required": sorted(
                (entry.skill, entry.importance) for entry in job.required_skills
            ),
            "preferred": sorted(job.preferred_skills),
            "domains": sorted(job.domains),
            "responsibilities": job.responsibilities,
        },
        "candidate": {
            "title": profile.current_title,
            "years": profile.total_years_experience,
            "skills": sorted(profile.skills),
            "domains": sorted(profile.domains),
            "education": sorted(profile.education),
            "certifications": sorted(profile.certifications),
            # Experience entries are order-significant, so they are not sorted.
            "experience": [entry.model_dump() for entry in profile.experience],
        },
        # The evaluator reads the resume text directly, so it is part of the input.
        "resume_sha": hashlib.sha256(resume_text.encode("utf-8")).hexdigest(),
        "model": settings.recruiter_model,
        "prompt_version": PROMPT_VERSION,
    }
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def rerank_fingerprint(job: ParsedJobDescription, candidates: list) -> str:
    """Hash for one rerank call.

    Reranking is a single call over the whole survivor set, so the key covers the
    job plus every candidate's ordering-relevant fields. Order-insensitive: the same
    set retrieved in a different order is the same question.

    Prefixed to keep it in a separate key space from evaluations, which share the
    table — the stored payload's shape follows the prefix.
    """
    payload = {
        "job": {
            "title": job.title,
            "seniority": job.seniority,
            "min_years": job.min_years_experience,
            "required": sorted(
                (entry.skill, entry.importance) for entry in job.required_skills
            ),
            "preferred": sorted(job.preferred_skills),
            "domains": sorted(job.domains),
        },
        "candidates": sorted(
            [
                candidate.candidate_id,
                candidate.title or "",
                round(float(candidate.years), 1),
                sorted(candidate.skills),
                sorted(candidate.domains),
            ]
            for candidate in candidates
        ),
        "model": settings.recruiter_model,
        "prompt_version": PROMPT_VERSION,
    }
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return "rerank:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:57]


def get_rerank(session: Session, key: str) -> RerankResponse | None:
    row = session.execute(
        select(EvaluationCache).where(EvaluationCache.fingerprint == key)
    ).scalars().first()
    if row is None:
        return None
    try:
        result = RerankResponse.model_validate(row.evaluation)
    except Exception:  # noqa: BLE001
        logger.warning("Discarding unreadable cached rerank %s", key[:16])
        return None
    session.execute(
        update(EvaluationCache)
        .where(EvaluationCache.id == row.id)
        .values(hits=EvaluationCache.hits + 1)
    )
    session.commit()
    return result


def put_rerank(session: Session, key: str, response: RerankResponse) -> None:
    session.add(
        EvaluationCache(
            fingerprint=key,
            model=settings.recruiter_model,
            prompt_version=PROMPT_VERSION,
            evaluation=response.model_dump(mode="json"),
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()


def get(session: Session, key: str) -> CandidateEvaluation | None:
    """Return a cached evaluation, or None. Never raises — a cache miss and a
    corrupt row are the same thing to the caller: evaluate again."""
    row = session.execute(
        select(EvaluationCache).where(EvaluationCache.fingerprint == key)
    ).scalars().first()
    if row is None:
        return None

    try:
        evaluation = CandidateEvaluation.model_validate(row.evaluation)
    except Exception:  # noqa: BLE001 - a stale shape must not break a screening
        logger.warning("Discarding unreadable cached evaluation %s", key[:12])
        return None

    # Counted with an UPDATE rather than a read-modify-write so parallel evaluation
    # does not lose increments.
    session.execute(
        update(EvaluationCache)
        .where(EvaluationCache.id == row.id)
        .values(hits=EvaluationCache.hits + 1)
    )
    session.commit()
    return evaluation


def put(session: Session, key: str, evaluation: CandidateEvaluation) -> None:
    """Store an evaluation. A concurrent duplicate is not an error."""
    session.add(
        EvaluationCache(
            fingerprint=key,
            model=settings.recruiter_model,
            prompt_version=PROMPT_VERSION,
            evaluation=evaluation.model_dump(mode="json"),
        )
    )
    try:
        session.commit()
    except IntegrityError:
        # Another worker evaluated the same pair first. Theirs is as good as ours.
        session.rollback()

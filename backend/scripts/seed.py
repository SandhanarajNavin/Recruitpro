"""Seed a recruiter account and populate the candidate repository.

Runs the resumes through the real ingestion path — store, extract, parse, embed —
rather than inserting rows directly, so a successful seed is also a proof that the
pipeline works end to end.

    python scripts/seed.py
    python scripts/seed.py --screen     # also run one screening against the sample JD
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from seed_data import SAMPLE_CANDIDATES, SAMPLE_JOB_DESCRIPTION  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.logging import configure_logging, get_logger  # noqa: E402
from app.db.database import session_scope  # noqa: E402
from app.db.models import Candidate, User  # noqa: E402
from app.db.models.user import UserRole  # noqa: E402
from app.services import job_service, matching_service, resume_service  # noqa: E402

logger = get_logger("seed")

DEMO_EMAIL = "recruiter@example.com"
DEMO_PASSWORD = "recruiter123"


def ensure_user(session) -> User:
    user = session.execute(select(User).where(User.email == DEMO_EMAIL)).scalars().first()
    if user is not None:
        logger.info("Recruiter already exists: %s", DEMO_EMAIL)
        return user

    from app.core.security import hash_password

    user = User(
        name="Demo Recruiter",
        email=DEMO_EMAIL,
        password_hash=hash_password(DEMO_PASSWORD),
        role=UserRole.RECRUITER.value,
    )
    session.add(user)
    session.commit()
    logger.info("Created recruiter %s / %s", DEMO_EMAIL, DEMO_PASSWORD)
    return user


def ingest_candidates(session, user: User) -> int:
    existing = session.execute(
        select(Candidate).where(Candidate.owner_id == user.id)
    ).scalars().all()
    if existing:
        logger.info("Repository already holds %s candidates; skipping ingestion.", len(existing))
        return len(existing)

    ingested = 0
    for _external_id, name, resume_text in SAMPLE_CANDIDATES:
        filename = f"{name.replace(' ', '_')}.txt"
        resume = resume_service.accept_upload(
            session,
            owner_id=user.id,
            filename=filename,
            content_type="text/plain",
            data=resume_text.encode("utf-8"),
        )
        # Inline rather than queued: a seed should be finished when it returns.
        resume_service.process_resume(session, resume.id)
        session.refresh(resume)
        if resume.status == "ready":
            ingested += 1
            logger.info("  ✓ %-22s %s", name, resume.status)
        else:
            logger.warning("  ✗ %-22s %s — %s", name, resume.status, resume.error)
    return ingested


def run_demo_screening(session, user: User) -> None:
    job = job_service.create_job(
        session,
        owner_id=user.id,
        title="Senior Full-Stack Engineer, Payments Platform",
        description=SAMPLE_JOB_DESCRIPTION,
    )
    logger.info("Created job %s (parser: %s)", job.id, job.requirement.parser_model)
    logger.info(
        "  required skills: %s",
        ", ".join(entry["skill"] for entry in job.requirement.required_skills),
    )
    logger.info("  hard filters: %s", job.requirement.hard_filters)

    screening = matching_service.create_screening(session, job=job)
    matching_service.run_screening(session, screening.id)
    session.refresh(screening)

    logger.info(
        "Screening %s — status=%s mode=%s", screening.id, screening.status, screening.mode
    )
    logger.info(
        "  funnel: pool=%s filtered=%s retrieved=%s reranked=%s evaluated=%s",
        screening.pool_size,
        screening.filtered_count,
        screening.retrieved_count,
        screening.reranked_count,
        screening.evaluated_count,
    )
    if screening.degradations:
        for note in screening.degradations:
            logger.warning("  degraded: %s", note)

    results = matching_service.get_results(session, screening.id, shortlist_only=True)
    logger.info("  shortlist:")
    for result in results:
        explanation = result.explanation
        logger.info(
            "    %s. %-22s %6.2f  %-12s",
            result.rank,
            session.get(Candidate, result.candidate_id).full_name,
            float(result.composite_score),
            result.recommendation,
        )
        if explanation and explanation.why_not:
            logger.info("        gap: %s", explanation.why_not[0][:96])


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Seed the recruitment repository.")
    parser.add_argument(
        "--screen", action="store_true", help="Also create the sample job and run a screening."
    )
    args = parser.parse_args()

    logger.info("Mode: %s | embeddings: %s", settings.mode, settings.embedding_model)

    session = session_scope()
    try:
        user = ensure_user(session)
        count = ingest_candidates(session, user)
        logger.info("Repository holds %s candidates.", count)
        if args.screen:
            run_demo_screening(session, user)
    finally:
        session.close()

    logger.info("Done. Log in as %s / %s", DEMO_EMAIL, DEMO_PASSWORD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Re-embed everything with the currently configured embedder.

Run this after changing EMBEDDING_PROVIDER or EMBEDDING_MODEL. Vectors are only ever
compared within one model — retrieval filters on ``embeddings.model`` — so switching
models does not corrupt anything, it makes the old vectors invisible. Until this has
run, a screening retrieves nobody and passage search finds nothing.

Three kinds of vector:

* candidate  — one per person, built from their latest parsed profile
* job        — one per role, built from its parsed requirements
* passage    — many per resume, built from the resume's own words

Idempotent: candidate and job vectors upsert per (owner, model) and passages are
replaced per resume, so re-running is safe.

    python scripts/backfill_embeddings.py            # what the current model lacks
    python scripts/backfill_embeddings.py --rebuild  # every row, even if present
    python scripts/backfill_embeddings.py --only passages
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.ai.embeddings import get_embedder  # noqa: E402
from app.ai.retrieval import passage_search  # noqa: E402
from app.ai.schemas import EmploymentEntry, ParsedResume  # noqa: E402
from app.db.database import session_scope  # noqa: E402
from app.db.models import (  # noqa: E402
    Candidate,
    CandidateProfile,
    Embedding,
    EmbeddingOwner,
    Job,
    Resume,
    ResumeChunk,
    ResumeStatus,
)
from app.parsers.jd_parser import job_embedding_text  # noqa: E402
from app.parsers.resume_parser import profile_text_for_embedding  # noqa: E402
from app.services.matching_service import requirement_to_parsed  # noqa: E402


def _as_parsed(candidate: Candidate, profile: CandidateProfile) -> ParsedResume:
    """A stored profile in the shape ``profile_text_for_embedding`` expects.

    Rebuilt rather than re-derived from text so the embedding input is byte-identical
    to what ingestion produced — a backfill that embeds slightly different text
    silently gives one candidate a vector nobody else's is comparable to.
    """
    return ParsedResume(
        full_name=candidate.full_name,
        email=candidate.email,
        phone=candidate.phone,
        location=candidate.location,
        current_title=profile.current_title,
        total_years_experience=float(profile.total_years_experience or 0),
        summary=candidate.summary,
        skills=list(profile.skills or []),
        domains=list(profile.domains or []),
        experience=[
            EmploymentEntry(
                title=str(item.get("title") or ""),
                company=str(item.get("company") or ""),
                start_year=item.get("start_year"),
                end_year=item.get("end_year"),
                highlights=list(item.get("highlights") or []),
            )
            for item in (profile.experience or [])
        ],
        education=list(profile.education or []),
        certifications=list(profile.certifications or []),
        projects=list(profile.projects or []),
        achievements=list(profile.achievements or []),
    )


def _existing(session, owner_type: str, model: str) -> set:
    return {
        row[0]
        for row in session.execute(
            select(Embedding.owner_id).where(
                Embedding.owner_type == owner_type, Embedding.model == model
            )
        ).all()
    }


def _upsert(session, owner_type: str, owner_id, vector, embedder) -> None:
    existing = session.execute(
        select(Embedding).where(
            Embedding.owner_type == owner_type,
            Embedding.owner_id == owner_id,
            Embedding.model == embedder.model,
        )
    ).scalars().first()
    if existing is not None:
        existing.vector = vector
        existing.dim = embedder.dim
        existing.version += 1
        return
    session.add(
        Embedding(
            owner_type=owner_type,
            owner_id=owner_id,
            vector=vector,
            model=embedder.model,
            dim=embedder.dim,
        )
    )


def candidates(session, embedder, rebuild: bool) -> tuple[int, int]:
    done = skipped = 0
    have = set() if rebuild else _existing(session, EmbeddingOwner.CANDIDATE.value, embedder.model)
    for candidate in session.execute(select(Candidate)).scalars():
        profile = candidate.latest_profile
        if profile is None:
            continue  # never parsed; nothing to embed
        if candidate.id in have:
            skipped += 1
            continue
        text = profile_text_for_embedding(_as_parsed(candidate, profile))
        _upsert(
            session, EmbeddingOwner.CANDIDATE.value, candidate.id, embedder.embed(text), embedder
        )
        session.commit()
        done += 1
        print(f"  candidate  {candidate.full_name[:44]:44} ok")
    return done, skipped


def jobs(session, embedder, rebuild: bool) -> tuple[int, int]:
    done = skipped = 0
    have = set() if rebuild else _existing(session, EmbeddingOwner.JOB.value, embedder.model)
    for job in session.execute(select(Job)).scalars():
        if job.requirement is None:
            continue  # never parsed
        if job.id in have:
            skipped += 1
            continue
        text = job_embedding_text(requirement_to_parsed(job))
        _upsert(session, EmbeddingOwner.JOB.value, job.id, embedder.embed(text), embedder)
        session.commit()
        done += 1
        print(f"  job        {job.title[:44]:44} ok")
    return done, skipped


def passages(session, embedder, rebuild: bool) -> tuple[int, int]:
    done = skipped = 0
    indexed_resumes = (
        set()
        if rebuild
        else {
            row[0]
            for row in session.execute(
                select(ResumeChunk.resume_id)
                .join(Embedding, Embedding.owner_id == ResumeChunk.id)
                .where(
                    Embedding.owner_type == EmbeddingOwner.RESUME_CHUNK.value,
                    Embedding.model == embedder.model,
                )
                .group_by(ResumeChunk.resume_id)
            ).all()
        }
    )
    resumes = session.execute(
        select(Resume).where(
            Resume.status == ResumeStatus.READY.value, Resume.extracted_text.isnot(None)
        )
    ).scalars().all()
    for resume in resumes:
        if resume.id in indexed_resumes:
            skipped += 1
            continue
        count = passage_search.index_resume(session, resume, resume.candidate_id)
        session.commit()
        done += 1
        print(f"  passages   {resume.original_filename[:44]:44} {count:3}")
    return done, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true", help="Re-embed rows that already have a vector for this model.")
    parser.add_argument(
        "--only",
        choices=["candidates", "jobs", "passages"],
        help="Limit to one kind of vector.",
    )
    args = parser.parse_args()

    session = session_scope()
    embedder = get_embedder()
    print(f"Embedder: {embedder.model} (dim={embedder.dim})")
    if embedder.model.startswith("offline"):
        print(
            "  NOTE: the offline hashing embedder is active. Passages will match on\n"
            "  word overlap rather than meaning. Set EMBEDDING_PROVIDER=gemini and\n"
            "  re-run with --rebuild."
        )
    print()

    steps = {"candidates": candidates, "jobs": jobs, "passages": passages}
    if args.only:
        steps = {args.only: steps[args.only]}

    for name, step in steps.items():
        done, skipped = step(session, embedder, args.rebuild)
        print(f"{name:11} embedded {done}, skipped {skipped} already current\n")

    total_chunks = session.execute(select(func.count()).select_from(ResumeChunk)).scalar()
    by_model = session.execute(
        select(Embedding.owner_type, Embedding.model, func.count())
        .group_by(Embedding.owner_type, Embedding.model)
        .order_by(Embedding.owner_type)
    ).all()
    print(f"{total_chunks} passages stored. Vectors by owner and model:")
    for owner_type, model, count in by_model:
        current = " <- current" if model == embedder.model else ""
        print(f"  {owner_type:13} {model:26} {count:6}{current}")
    print(
        "\nVectors from other models are inert, not harmful: retrieval only compares\n"
        "within one model. Delete them once you are sure you will not switch back."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

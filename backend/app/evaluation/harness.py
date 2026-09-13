"""Search-quality harness (architecture doc §27).

Runs the golden set through the funnel and reports ranking metrics per stage, so a
change to embeddings, filters, reranking or scoring can be shown to help rather than
assumed to.

The stages are separable on purpose. §27 asks to "compare vector-only,
structured-only and hybrid retrieval", and the only way to know which part of the
pipeline moved a number is to measure each in isolation:

    vector     ANN similarity alone, no gates            — cheap, no model calls
    hybrid     hard filters + ANN                        — cheap
    rerank     hybrid, then lexical re-scoring           — cheap
    full       the whole funnel including LLM evaluation — slow and billable

Only ``full`` costs money. The first three are the ones to run on every change.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.ranking import candidate_ranker
from app.ai.retrieval import vector_search
from app.core.logging import get_logger
from app.db.models import Candidate, Job, ScreeningResult, User
from app.db.repositories import candidate_repository
from app.evaluation.metrics import evaluate_ranking, mean_scores
from app.services import matching_service

logger = get_logger(__name__)

STAGES = ("vector", "hybrid", "rerank", "full")

#: Golden set location, relative to the backend root.
DEFAULT_GOLDEN = Path(__file__).resolve().parents[2] / "evaluation" / "golden.yaml"


class GoldenSetError(RuntimeError):
    """The golden set does not line up with the database."""


@dataclass
class JobResult:
    job_title: str
    ranked: list[str]
    ranked_names: list[str]
    relevance: dict[str, int]
    scores: dict[str, float]
    note: str = ""


@dataclass
class StageResult:
    stage: str
    per_job: list[JobResult] = field(default_factory=list)
    overall: dict[str, float] = field(default_factory=dict)


def load_golden(path: Path | None = None) -> dict:
    path = path or DEFAULT_GOLDEN
    if not path.exists():
        raise GoldenSetError(
            f"No golden set at {path}. Run scripts/evaluate_search.py --scaffold first."
        )
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _resolve_candidates(session: Session, owner_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """Name -> id. The golden set keys on names so it stays hand-editable and
    survives a database reset, which UUIDs would not."""
    rows = session.execute(
        select(Candidate.full_name, Candidate.id).where(Candidate.owner_id == owner_id)
    ).all()
    index: dict[str, uuid.UUID] = {}
    for name, cid in rows:
        key = name.strip().lower()
        if key in index:
            logger.warning("Duplicate candidate name %r — golden set may be ambiguous", name)
        index[key] = cid
    return index


def _resolve_job(session: Session, owner_id: uuid.UUID, title: str) -> Job:
    job = session.execute(
        select(Job).where(Job.owner_id == owner_id, Job.title == title)
    ).scalars().first()
    if job is None:
        raise GoldenSetError(f"No job titled {title!r} for this recruiter.")
    if job.requirement is None:
        raise GoldenSetError(f"Job {title!r} has no parsed requirements.")
    return job


def _rank_vector(session: Session, job: Job, owner_id: uuid.UUID) -> list[uuid.UUID]:
    """ANN similarity with the gates switched off — pure embedding quality."""
    parsed = matching_service.requirement_to_parsed(job)
    rows = vector_search.search(
        session,
        owner_id=owner_id,
        job=parsed,
        must_have_skills=[],
        min_years=0,
        limit=100,
    )
    return [row.candidate_id for row in rows]


def _rank_hybrid(session: Session, job: Job, owner_id: uuid.UUID) -> list[uuid.UUID]:
    """Hard filters applied, then ANN ordering — what production retrieval does."""
    parsed = matching_service.requirement_to_parsed(job)
    filters = job.requirement.hard_filters or {}
    must_have = [str(s) for s in (filters.get("must_have_skills") or [])]
    min_years = int(filters.get("min_years") or 0)

    survivors = candidate_repository.count_after_filters(
        session, owner_id, must_have_skills=must_have, min_years=min_years
    )
    # Mirrors the funnel: a gate that empties the pool is relaxed once, because it
    # is nearly always a parsing artefact rather than "nobody qualifies".
    if survivors == 0:
        must_have, min_years = [], 0

    rows = vector_search.search(
        session,
        owner_id=owner_id,
        job=parsed,
        must_have_skills=must_have,
        min_years=min_years,
        limit=100,
    )
    return [row.candidate_id for row in rows]


def _rank_rerank(session: Session, job: Job, owner_id: uuid.UUID) -> list[uuid.UUID]:
    """Hybrid retrieval, then lexical re-scoring."""
    parsed = matching_service.requirement_to_parsed(job)
    ids = _rank_hybrid(session, job, owner_id)
    if not ids:
        return []

    # The reranker scores from the retrieval row alone (title, years, skills,
    # domains) — the same inputs the funnel gives it — so no survivor detail load
    # is needed here.
    profiles = {
        row.candidate_id: row
        for row in vector_search.search(
            session,
            owner_id=owner_id,
            job=parsed,
            must_have_skills=[],
            min_years=0,
            limit=100,
        )
    }

    inputs = []
    for cid in ids:
        row = profiles.get(cid)
        if row is None:
            continue
        inputs.append(
            candidate_ranker.RerankCandidate(
                candidate_id=str(cid),
                title=row.current_title or "Not stated",
                years=row.years,
                skills=row.skills,
                domains=row.domains,
                summary=(
                    f"{row.current_title or 'Not stated'} with {row.years:.0f} years. "
                    f"Skills: {', '.join(row.skills[:18])}."
                ),
            )
        )
    ranked, _engine = candidate_ranker.rerank(parsed, inputs)
    return [uuid.UUID(entry.candidate_id) for entry in ranked]


def _rank_full(session: Session, job: Job, owner_id: uuid.UUID) -> list[uuid.UUID]:
    """The whole funnel. Costs a model call per surviving candidate."""
    screening = matching_service.create_screening(session, job=job)
    matching_service.run_screening(session, screening.id)
    rows = session.execute(
        select(ScreeningResult)
        .where(ScreeningResult.screening_id == screening.id)
        .order_by(ScreeningResult.rank)
    ).scalars().all()
    return [row.candidate_id for row in rows]


_RANKERS = {
    "vector": _rank_vector,
    "hybrid": _rank_hybrid,
    "rerank": _rank_rerank,
    "full": _rank_full,
}


def run_stage(
    session: Session,
    *,
    stage: str,
    golden: dict,
    recruiter_email: str,
    ks: tuple[int, ...] = (3, 5),
) -> StageResult:
    if stage not in _RANKERS:
        raise GoldenSetError(f"Unknown stage {stage!r}; expected one of {STAGES}.")

    user = session.execute(
        select(User).where(User.email == recruiter_email)
    ).scalars().first()
    if user is None:
        raise GoldenSetError(f"No recruiter {recruiter_email!r}.")

    by_name = _resolve_candidates(session, user.id)
    id_to_name = {cid: name for name, cid in by_name.items()}
    result = StageResult(stage=stage)

    for entry in golden.get("jobs", []):
        title = entry["title"]
        job = _resolve_job(session, user.id, title)

        relevance: dict[str, int] = {}
        for row in entry.get("candidates", []):
            key = row["name"].strip().lower()
            cid = by_name.get(key)
            if cid is None:
                raise GoldenSetError(
                    f"Golden set names {row['name']!r} for {title!r}, "
                    "but no such candidate belongs to this recruiter."
                )
            relevance[str(cid)] = int(row["grade"])

        ranked_ids = _RANKERS[stage](session, job, user.id)
        ranked = [str(cid) for cid in ranked_ids]
        note = ""
        if not ranked:
            note = "retrieved nothing"

        result.per_job.append(
            JobResult(
                job_title=title,
                ranked=ranked,
                ranked_names=[
                    id_to_name.get(cid, str(cid)[:8]) for cid in ranked_ids
                ],
                relevance=relevance,
                scores=evaluate_ranking(ranked, relevance, ks=ks),
                note=note,
            )
        )

    result.overall = mean_scores([job.scores for job in result.per_job])
    return result

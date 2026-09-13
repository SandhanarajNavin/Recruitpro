"""The matching funnel (architecture doc §10).

    pool → hard filters → ANN retrieval → rerank → evaluate → score → top N

The shape is what bounds cost: the LLM only ever sees ``rerank_limit`` candidates,
so a run against 100 candidates and a run against 100,000 cost roughly the same in
model tokens. Everything before the evaluate stage is SQL, vector math, or one cheap
batched call.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.ai.llm import evaluator, llm_available
from app.ai.ranking import candidate_ranker as reranker
from app.ai.retrieval import vector_search
from app.ai.schemas import (
    CandidateEvaluation,
    EmploymentEntry,
    ParsedJobDescription,
    ParsedResume,
    RerankEntry,
    RerankResponse,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.screening import ScreeningStage
from app.db.repositories import candidate_repository as candidate_repo
from app.services import evaluation_cache
from app.services.scoring import ScoreBreakdown, compute_score

logger = get_logger(__name__)

StageCallback = Callable[[ScreeningStage, dict[str, int]], None]


@dataclass
class ScoredCandidate:
    candidate_id: uuid.UUID
    profile_id: uuid.UUID
    name: str
    similarity: float
    rerank_score: float
    evaluation: CandidateEvaluation
    breakdown: ScoreBreakdown
    evaluated_by: str


@dataclass
class FunnelResult:
    scored: list[ScoredCandidate] = field(default_factory=list)
    pool_size: int = 0
    filtered_count: int = 0
    retrieved_count: int = 0
    reranked_count: int = 0
    evaluated_count: int = 0
    degradations: list[str] = field(default_factory=list)
    mode: str = "offline"


def run_funnel(
    session: Session,
    *,
    owner_id: uuid.UUID,
    job: ParsedJobDescription,
    hard_filters: dict[str, object],
    on_stage: StageCallback | None = None,
    job_id: uuid.UUID | None = None,
) -> FunnelResult:
    result = FunnelResult(mode="gemini" if llm_available() else "offline")

    def stage(name: ScreeningStage, **counts: int) -> None:
        if on_stage:
            on_stage(name, counts)

    must_have = [str(skill) for skill in (hard_filters.get("must_have_skills") or [])]
    min_years = int(hard_filters.get("min_years") or 0)

    # ── stage 0-1: pool and hard gates ────────────────────────────────────
    stage(ScreeningStage.FILTERING)
    result.pool_size = candidate_repo.count_pool(session, owner_id, job_id=job_id)
    result.filtered_count = candidate_repo.count_after_filters(
        session, owner_id, must_have_skills=must_have, min_years=min_years, job_id=job_id
    )

    if result.filtered_count == 0 and result.pool_size > 0:
        # A gate that empties the pool is nearly always a parsing artefact rather
        # than a real "nobody qualifies", so retry once without the gates and say so.
        logger.warning("Hard filters excluded every candidate; retrying without gates.")
        requirement = " and ".join(
            filter(
                None,
                [
                    ", ".join(must_have) if must_have else "",
                    f"{min_years}+ years" if min_years else "",
                ],
            )
        )
        result.degradations.append(
            f"No candidate met every hard requirement ({requirement}), so this run "
            "ranked the whole pool instead. Scores still reflect those requirements."
            if requirement
            else "No candidate met the hard requirements, so this run ranked the "
            "whole pool instead."
        )
        must_have, min_years = [], 0
        result.filtered_count = candidate_repo.count_after_filters(
            session, owner_id, must_have_skills=[], min_years=0, job_id=job_id
        )

    # ── stage 2: semantic retrieval ───────────────────────────────────────
    stage(ScreeningStage.RETRIEVING, filtered=result.filtered_count)
    retrieved = vector_search.search(
        session,
        owner_id=owner_id,
        job=job,
        must_have_skills=must_have,
        min_years=min_years,
        limit=settings.retrieval_limit,
        job_id=job_id,
    )
    result.retrieved_count = len(retrieved)
    if not retrieved:
        return result

    # ── stage 3: rerank ───────────────────────────────────────────────────
    stage(ScreeningStage.RERANKING, retrieved=result.retrieved_count)
    rerank_inputs = [
        reranker.RerankCandidate(
            candidate_id=str(row.candidate_id),
            title=row.current_title or "Not stated",
            years=row.years,
            skills=row.skills,
            domains=row.domains,
            summary=(
                f"{row.current_title or 'Not stated'} with {row.years:.0f} years. "
                f"Skills: {', '.join(row.skills[:18])}."
            ),
        )
        for row in retrieved
    ]
    # Cached like evaluation, and for the same reason: rerank order decides who
    # reaches the evaluate stage once the pool exceeds rerank_limit, so a wobbling
    # order silently changes which candidates are ever considered.
    rerank_key = evaluation_cache.rerank_fingerprint(job, rerank_inputs)
    cached_rerank = evaluation_cache.get_rerank(session, rerank_key)
    if cached_rerank is not None:
        by_candidate = {entry.candidate_id: entry for entry in cached_rerank.rankings}
        ranked = sorted(
            (
                reranker.RerankResult(
                    candidate.candidate_id,
                    by_candidate[candidate.candidate_id].relevance
                    if candidate.candidate_id in by_candidate
                    else 50.0,
                    by_candidate[candidate.candidate_id].rationale
                    if candidate.candidate_id in by_candidate
                    else "Not scored by the reranker.",
                )
                for candidate in rerank_inputs
            ),
            key=lambda item: item.relevance,
            reverse=True,
        )
        reranker_name = f"{settings.recruiter_model} (cached)"
    else:
        ranked, reranker_name = reranker.rerank(job, rerank_inputs)
        if not reranker_name.startswith("lexical"):
            evaluation_cache.put_rerank(
                session,
                rerank_key,
                RerankResponse(
                    rankings=[
                        RerankEntry(
                            candidate_id=item.candidate_id,
                            relevance=item.relevance,
                            rationale=item.rationale,
                        )
                        for item in ranked
                    ]
                ),
            )

    if reranker_name == "lexical-rerank-v1" and result.mode == "gemini":
        result.degradations.append("Reranking used the lexical engine (Gemini rerank failed).")

    survivors = ranked[: settings.rerank_limit]
    result.reranked_count = len(survivors)

    by_id = {str(row.candidate_id): row for row in retrieved}
    rerank_by_id = {entry.candidate_id: entry.relevance for entry in survivors}

    # ── stage 4: requirement evaluation ───────────────────────────────────
    stage(ScreeningStage.EVALUATING, reranked=result.reranked_count)
    survivor_ids = [uuid.UUID(entry.candidate_id) for entry in survivors]
    details = candidate_repo.load_survivor_details(session, survivor_ids)

    # Each candidate is an independent model call, so evaluating them in series
    # made the stage cost N x latency — four minutes for three candidates. The DB
    # work is already done (``details`` is loaded above) and ``evaluate`` touches no
    # session, so the calls can overlap safely. Ordering is restored by the sort in
    # stage 5, which keys on the composite score rather than arrival.
    inputs = []
    for entry in survivors:
        row = by_id[entry.candidate_id]
        detail = details.get(row.candidate_id)
        inputs.append(
            (
                entry,
                row,
                _build_profile(row, detail),
                detail.resume_text if detail else "",
            )
        )

    # Cache lookups happen here, on the request session, before anything is handed
    # to the pool — a SQLAlchemy Session is not thread-safe, so the threads only
    # ever run the model call itself.
    keys = {
        entry.candidate_id: evaluation_cache.fingerprint(job, profile, resume_text)
        for entry, _row, profile, resume_text in inputs
    }
    cached = {}
    for entry, _row, _profile, _text in inputs:
        hit = evaluation_cache.get(session, keys[entry.candidate_id])
        if hit is not None:
            cached[entry.candidate_id] = hit

    pending = [item for item in inputs if item[0].candidate_id not in cached]
    if cached:
        logger.info(
            "Evaluation cache: %s hit(s), %s to evaluate", len(cached), len(pending)
        )

    def _evaluate_one(item):
        entry, row, profile, resume_text = item
        evaluation, engine = evaluator.evaluate(job, profile, resume_text)
        return entry, row, evaluation, engine

    workers = max(1, min(settings.evaluation_concurrency, len(pending)))
    llm_failures = 0
    fresh: list[tuple] = []
    if pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            fresh = list(pool.map(_evaluate_one, pending))

    # Written after the pool joins, again to keep the session on one thread. Only
    # model-produced evaluations are cached; the offline engine is already
    # deterministic and caching it would hide a later credential fix.
    for entry, _row, evaluation, engine in fresh:
        if not engine.startswith("offline"):
            evaluation_cache.put(session, keys[entry.candidate_id], evaluation)

    evaluated = fresh + [
        (entry, row, cached[entry.candidate_id], f"{settings.recruiter_model} (cached)")
        for entry, row, _profile, _text in inputs
        if entry.candidate_id in cached
    ]

    for entry, row, evaluation, engine in evaluated:
        if engine.startswith("offline") and result.mode == "gemini":
            llm_failures += 1

        breakdown = compute_score(evaluation, cosine_similarity=row.similarity)
        result.scored.append(
            ScoredCandidate(
                candidate_id=row.candidate_id,
                profile_id=row.profile_id,
                name=row.full_name,
                similarity=row.similarity,
                rerank_score=rerank_by_id.get(entry.candidate_id, 0.0),
                evaluation=evaluation,
                breakdown=breakdown,
                evaluated_by=engine,
            )
        )

    result.evaluated_count = len(result.scored)
    if llm_failures:
        result.degradations.append(
            f"{llm_failures} of {result.evaluated_count} candidates fell back to rules-based "
            "evaluation."
        )

    # ── stage 5: score and order ──────────────────────────────────────────
    stage(ScreeningStage.SCORING, evaluated=result.evaluated_count)
    result.scored.sort(key=lambda item: item.breakdown.composite, reverse=True)
    return result


def _build_profile(
    row: candidate_repo.RetrievedCandidate,
    detail: candidate_repo.SurvivorDetail | None,
) -> ParsedResume:
    """Assemble the evaluator's view of a candidate.

    The ordering fields come from the retrieval row (already in hand) and the
    evidence fields from the survivor detail load, so neither query fetches data the
    other already has.
    """
    return ParsedResume(
        full_name=row.full_name,
        email=None,
        phone=None,
        location=None,
        current_title=row.current_title,
        total_years_experience=row.years,
        summary=None,
        skills=row.skills,
        domains=row.domains,
        experience=[
            EmploymentEntry(
                title=str(item.get("title") or ""),
                company=str(item.get("company") or ""),
                start_year=item.get("start_year"),
                end_year=item.get("end_year"),
                highlights=list(item.get("highlights") or []),
            )
            for item in (detail.experience if detail else [])
        ],
        education=detail.education if detail else [],
        certifications=detail.certifications if detail else [],
        projects=detail.projects if detail else [],
        achievements=detail.achievements if detail else [],
    )

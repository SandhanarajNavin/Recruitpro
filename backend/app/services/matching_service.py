"""Screening orchestration: run the funnel, persist one immutable result set.

A screening is a record of a decision, so it stores the weights it used and the mode
it ran in. Re-running a job creates a new screening rather than editing this one —
last month's shortlist stays reconstructible with the numbers it was actually built
from.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.ai.llm.explainer import ExplanationInput, explain_shortlist
from app.ai.schemas import ParsedJobDescription, RequiredSkill
from app.core.config import settings
from app.core.logging import audit, get_logger
from app.db.models import Explanation, Job, Screening, ScreeningResult
from app.db.models.screening import ScreeningStage, ScreeningStatus
from app.services.funnel import run_funnel

logger = get_logger(__name__)


class ScreeningError(RuntimeError):
    pass


def create_screening(session: Session, *, job: Job) -> Screening:
    screening = Screening(
        job_id=job.id,
        status=ScreeningStatus.QUEUED.value,
        stage=ScreeningStage.QUEUED.value,
        mode=settings.mode,
        weights=settings.weights.as_dict(),
    )
    session.add(screening)
    session.commit()
    audit("screening.created", screening_id=screening.id, job_id=job.id)
    return screening


def requirement_to_parsed(job: Job) -> ParsedJobDescription:
    """Rebuild the parsed JD from its stored requirement row.

    The pipeline reads requirements from the database rather than re-parsing the
    description, so a recruiter's correction to the extracted requirements actually
    affects the next screening.
    """
    requirement = job.requirement
    if requirement is None:
        raise ScreeningError("Job has no parsed requirements; re-create the job.")

    return ParsedJobDescription(
        title=job.title,
        seniority=requirement.seniority or "Not stated",
        min_years_experience=requirement.min_years_experience,
        required_skills=[
            RequiredSkill(skill=str(entry.get("skill")), importance=int(entry.get("importance", 3)))
            for entry in requirement.required_skills
            if entry.get("skill")
        ],
        preferred_skills=list(requirement.preferred_skills or []),
        domains=list(requirement.domains or []),
        responsibilities=list(requirement.responsibilities or []),
        education=list(requirement.education or []),
        red_flags=list(requirement.red_flags or []),
    )


def run_screening(session: Session, screening_id: uuid.UUID) -> None:
    """Execute one screening end to end. Called by the worker (or inline in eager mode)."""
    screening = session.get(Screening, screening_id)
    if screening is None:
        logger.warning("run_screening called for unknown screening %s", screening_id)
        return

    job = session.get(Job, screening.job_id)
    if job is None:
        _fail(session, screening_id, "The job no longer exists.")
        return

    def on_stage(stage: ScreeningStage, counts: dict[str, int]) -> None:
        """Publish progress so `GET /screenings/{id}` reports a live stage."""
        screening.stage = stage.value
        for key, value in counts.items():
            column = f"{key}_count"
            if hasattr(screening, column):
                setattr(screening, column, value)
        session.commit()

    try:
        screening.status = ScreeningStatus.RUNNING.value
        screening.started_at = datetime.now(UTC)
        session.commit()

        parsed_job = requirement_to_parsed(job)
        funnel = run_funnel(
            session,
            owner_id=job.owner_id,
            job=parsed_job,
            hard_filters=dict(job.requirement.hard_filters or {}),
            on_stage=on_stage,
            job_id=job.id,
        )

        screening.pool_size = funnel.pool_size
        screening.filtered_count = funnel.filtered_count
        screening.retrieved_count = funnel.retrieved_count
        screening.reranked_count = funnel.reranked_count
        screening.evaluated_count = funnel.evaluated_count
        screening.degradations = funnel.degradations
        screening.mode = funnel.mode
        session.commit()

        shortlist = funnel.scored[: settings.shortlist_size]

        # ── explain (shortlist only) ──────────────────────────────────────
        screening.stage = ScreeningStage.EXPLAINING.value
        session.commit()

        explanation_inputs = [
            ExplanationInput(
                candidate_id=str(item.candidate_id),
                name=item.name,
                rank=index + 1,
                composite_score=item.breakdown.composite,
                subscores=item.breakdown.subscores,
                evaluation=item.evaluation,
            )
            for index, item in enumerate(shortlist)
        ]
        explanations, explainer_model = explain_shortlist(parsed_job, explanation_inputs)

        # ── persist ───────────────────────────────────────────────────────
        for index, item in enumerate(funnel.scored):
            rank = index + 1
            is_shortlisted = rank <= settings.shortlist_size

            result = ScreeningResult(
                screening_id=screening.id,
                candidate_id=item.candidate_id,
                profile_id=item.profile_id,
                rank=rank,
                composite_score=item.breakdown.composite,
                subscores=item.breakdown.subscores,
                evidence={
                    "categories": [
                        assessment.model_dump() for assessment in item.evaluation.categories
                    ],
                    "strengths": item.evaluation.strengths,
                    "concerns": item.evaluation.concerns,
                    "evaluated_by": item.evaluated_by,
                },
                matched_skills=item.evaluation.matched_skills,
                missing_skills=item.evaluation.missing_skills,
                retrieval_score=round(item.similarity, 4),
                rerank_score=round(item.rerank_score, 2),
                shortlisted=is_shortlisted,
                recommendation=item.breakdown.recommendation,
            )
            session.add(result)
            session.flush()

            if is_shortlisted:
                explanation = explanations.candidates.get(str(item.candidate_id))
                if explanation is not None:
                    session.add(
                        Explanation(
                            screening_result_id=result.id,
                            why_match=explanation.why_match,
                            why_not=explanation.why_not,
                            verdict=explanation.verdict,
                            model=explainer_model,
                        )
                    )

        screening.panel_summary = explanations.panel_summary
        screening.status = ScreeningStatus.COMPLETED.value
        screening.stage = ScreeningStage.DONE.value
        screening.finished_at = datetime.now(UTC)
        session.commit()

        audit(
            "screening.completed",
            screening_id=screening.id,
            job_id=job.id,
            mode=funnel.mode,
            pool=funnel.pool_size,
            evaluated=funnel.evaluated_count,
            shortlisted=len(shortlist),
            degradations=funnel.degradations,
        )

    except Exception as exc:  # noqa: BLE001 - a failed run must be reportable, not fatal
        session.rollback()
        logger.exception("Screening %s failed", screening_id)
        _fail(session, screening_id, f"{exc}")


def _fail(session: Session, screening_id: uuid.UUID, message: str) -> None:
    screening = session.get(Screening, screening_id)
    if screening is None:
        return
    screening.status = ScreeningStatus.FAILED.value
    screening.error = message[:2000]
    screening.finished_at = datetime.now(UTC)
    session.commit()
    audit("screening.failed", screening_id=screening_id, error=message[:500])


def get_screening(
    session: Session, screening_id: uuid.UUID, owner_id: uuid.UUID
) -> Screening | None:
    return session.execute(
        select(Screening)
        .join(Job)
        .where(Screening.id == screening_id, Job.owner_id == owner_id)
        .options(selectinload(Screening.job))
    ).scalars().first()


def get_results(
    session: Session, screening_id: uuid.UUID, *, shortlist_only: bool = False
) -> list[ScreeningResult]:
    statement = (
        select(ScreeningResult)
        .where(ScreeningResult.screening_id == screening_id)
        .options(selectinload(ScreeningResult.explanation))
        .order_by(ScreeningResult.rank)
    )
    if shortlist_only:
        statement = statement.where(ScreeningResult.shortlisted.is_(True))
    return list(session.execute(statement).scalars().all())

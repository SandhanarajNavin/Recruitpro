"""Screening endpoints (architecture doc §16).

Start returns 202 with an id; status reports the funnel stage; results serves the
ranked shortlist with the score breakdown and the evidence behind it.
"""

from __future__ import annotations

import uuid
from typing import NamedTuple

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import current_user
from app.core.config import settings
from app.core.logging import audit, get_logger
from app.db.database import get_session
from app.db.models import (
    ApplicationStage,
    Candidate,
    CandidateProfile,
    ScreeningResult,
    User,
)
from app.db.models.screening import Recommendation
from app.schemas import (
    ExplanationOut,
    FunnelOut,
    OverrideRequest,
    RankedCandidateOut,
    ScreeningCreateRequest,
    ScreeningOut,
    ScreeningResultsResponse,
    ScreeningStatusResponse,
    SubscoreOut,
)
from app.services import (
    application_service,
    exclusion_service,
    job_service,
    matching_service,
)
from app.services.application_service import ApplicationError
from app.services.scoring import subscore_rows
from app.workers.dispatch import enqueue_screening

logger = get_logger(__name__)

router = APIRouter(prefix="/screenings", tags=["screenings"])


def _funnel(screening, shortlisted: int) -> FunnelOut:
    return FunnelOut(
        pool=screening.pool_size,
        filtered=screening.filtered_count,
        retrieved=screening.retrieved_count,
        reranked=screening.reranked_count,
        evaluated=screening.evaluated_count,
        shortlisted=shortlisted,
    )


class _Identity(NamedTuple):
    """Who a result is about. Only ``name`` is guaranteed."""

    name: str
    role: str | None = None
    years: float | None = None
    location: str | None = None


UNKNOWN = _Identity(name="Unknown")


def _identities(session: Session, candidate_ids: list[uuid.UUID]) -> dict[uuid.UUID, _Identity]:
    """One query for every result row rather than a lazy load per row.

    The profile is joined at its latest version; a candidate with no parsed profile
    still resolves, just without the role and years.
    """
    if not candidate_ids:
        return {}

    latest = (
        select(
            CandidateProfile.candidate_id.label("candidate_id"),
            func.max(CandidateProfile.version).label("version"),
        )
        .where(CandidateProfile.candidate_id.in_(candidate_ids))
        .group_by(CandidateProfile.candidate_id)
        .subquery()
    )

    rows = session.execute(
        select(
            Candidate.id,
            Candidate.full_name,
            Candidate.location,
            CandidateProfile.primary_role,
            CandidateProfile.total_years_experience,
        )
        .outerjoin(latest, latest.c.candidate_id == Candidate.id)
        .outerjoin(
            CandidateProfile,
            (CandidateProfile.candidate_id == Candidate.id)
            & (CandidateProfile.version == latest.c.version),
        )
        .where(Candidate.id.in_(candidate_ids))
    ).all()

    return {
        row[0]: _Identity(
            name=row[1],
            location=row[2],
            role=row[3],
            years=float(row[4]) if row[4] is not None else None,
        )
        for row in rows
    }


def _to_ranked(result: ScreeningResult, identity: _Identity) -> RankedCandidateOut:
    subscores = [SubscoreOut(**row) for row in subscore_rows(result.subscores)]

    return RankedCandidateOut(
        rank=result.rank,
        candidate_id=result.candidate_id,
        name=identity.name,
        role=identity.role,
        years=identity.years,
        location=identity.location,
        composite_score=float(result.composite_score),
        recommendation=result.recommendation,
        # What the recruiter decided wins over what the pipeline suggested.
        effective_recommendation=result.override_recommendation or result.recommendation,
        shortlisted=result.shortlisted,
        subscores=subscores,
        matched_skills=list(result.matched_skills or []),
        missing_skills=list(result.missing_skills or []),
        retrieval_score=(
            float(result.retrieval_score) if result.retrieval_score is not None else None
        ),
        rerank_score=float(result.rerank_score) if result.rerank_score is not None else None,
        evidence=dict(result.evidence or {}),
        explanation=(
            ExplanationOut.model_validate(result.explanation) if result.explanation else None
        ),
        override_note=result.override_note,
    )


@router.post("", response_model=ScreeningStatusResponse, status_code=status.HTTP_202_ACCEPTED)
def start_screening(
    body: ScreeningCreateRequest,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ScreeningStatusResponse:
    job = job_service.get_job(session, body.job_id, user.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")
    if job.requirement is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Job has no parsed requirements; re-create the job."
        )

    screening = matching_service.create_screening(session, job=job)
    enqueue_screening(screening.id)
    session.refresh(screening)

    return ScreeningStatusResponse(
        screening=ScreeningOut.model_validate(screening),
        funnel=_funnel(screening, 0),
    )


@router.get("/{screening_id}", response_model=ScreeningStatusResponse)
def screening_status(
    screening_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ScreeningStatusResponse:
    screening = matching_service.get_screening(session, screening_id, user.id)
    if screening is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Screening not found.")

    shortlisted = sum(1 for result in screening.results if result.shortlisted)
    return ScreeningStatusResponse(
        screening=ScreeningOut.model_validate(screening),
        funnel=_funnel(screening, shortlisted),
    )


@router.get("/{screening_id}/results", response_model=ScreeningResultsResponse)
def screening_results(
    screening_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ScreeningResultsResponse:
    screening = matching_service.get_screening(session, screening_id, user.id)
    if screening is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Screening not found.")

    results = matching_service.get_results(session, screening_id)

    # Candidates removed from this job's rankings. A future run excludes them in SQL,
    # but this screening is already on disk and is served as stored, so they are
    # filtered here too — otherwise "remove" appears to do nothing until a re-run.
    hidden = exclusion_service.excluded_candidate_ids(session, screening.job_id)
    if hidden:
        results = [result for result in results if result.candidate_id not in hidden]

    identities = _identities(session, [result.candidate_id for result in results])
    ranked = [
        _to_ranked(result, identities.get(result.candidate_id, UNKNOWN)) for result in results
    ]
    shortlist = [entry for entry in ranked if entry.shortlisted]

    return ScreeningResultsResponse(
        screening=ScreeningOut.model_validate(screening),
        funnel=_funnel(screening, len(shortlist)),
        shortlist=shortlist,
        also_considered=[entry for entry in ranked if not entry.shortlisted][
            : settings.retrieval_limit
        ],
    )


@router.post("/{screening_id}/results/{candidate_id}/override", response_model=RankedCandidateOut)
def override_recommendation(
    screening_id: uuid.UUID,
    candidate_id: uuid.UUID,
    body: OverrideRequest,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> RankedCandidateOut:
    """Record a recruiter's decision over the pipeline's recommendation (doc §19).

    The computed score and evidence are left untouched — an override is an additional
    fact about the decision, not a rewrite of how it was reached.
    """
    screening = matching_service.get_screening(session, screening_id, user.id)
    if screening is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Screening not found.")

    result = next(
        (item for item in screening.results if item.candidate_id == candidate_id), None
    )
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate is not in this screening.")

    result.override_recommendation = body.recommendation
    result.override_note = body.note
    result.override_by = user.id
    session.commit()

    # A verdict of interview or better is a decision to pursue someone, so it opens
    # an application — this is the bridge from the matching engine into the hiring
    # pipeline. Idempotent, so overriding twice does not create two applications.
    # A weaker verdict deliberately does NOT open one: shortlisting is an action a
    # recruiter takes, not a side effect of scoring.
    if body.recommendation in (
        Recommendation.STRONG_HIRE.value,
        Recommendation.INTERVIEW.value,
    ):
        try:
            application_service.open_application(
                session,
                owner_id=user.id,
                candidate_id=candidate_id,
                job_id=screening.job_id,
                stage=ApplicationStage.SHORTLISTED,
                screening_result_id=result.id,
                actor_id=user.id,
            )
        except ApplicationError as exc:
            # The override itself succeeded and is committed; failing to open the
            # application must not lose it.
            logger.warning("Override recorded but application not opened: %s", exc)

    audit(
        "screening.override",
        screening_id=screening_id,
        candidate_id=candidate_id,
        user_id=user.id,
        was=result.recommendation,
        now=body.recommendation,
    )

    # The full identity, not just the name: the row this replaces on the client
    # shows role, years and location too, and _to_ranked expects an _Identity.
    identities = _identities(session, [candidate_id])
    return _to_ranked(result, identities.get(candidate_id, UNKNOWN))


@router.post(
    "/{screening_id}/results/{candidate_id}/hide", status_code=status.HTTP_204_NO_CONTENT
)
def hide_from_rankings(
    screening_id: uuid.UUID,
    candidate_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> None:
    """Remove a candidate from this job's rankings, now and on every future run.

    Scoped to the job, not the screening: the recruiter is saying "not for this
    role", which has to survive a re-run. The candidate, their resume and their
    standing in every other job are untouched, and it is reversible below.
    """
    screening = matching_service.get_screening(session, screening_id, user.id)
    if screening is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Screening not found.")

    exclusion_service.exclude(
        session, job_id=screening.job_id, candidate_id=candidate_id, actor_id=user.id
    )
    audit(
        "screening.candidate_hidden",
        screening_id=screening_id,
        job_id=screening.job_id,
        candidate_id=candidate_id,
        user_id=user.id,
    )


@router.delete(
    "/{screening_id}/results/{candidate_id}/hide", status_code=status.HTTP_204_NO_CONTENT
)
def restore_to_rankings(
    screening_id: uuid.UUID,
    candidate_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> None:
    """Undo a removal. The candidate returns to this job's next run."""
    screening = matching_service.get_screening(session, screening_id, user.id)
    if screening is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Screening not found.")

    if exclusion_service.restore(
        session, job_id=screening.job_id, candidate_id=candidate_id
    ):
        audit(
            "screening.candidate_restored",
            screening_id=screening_id,
            job_id=screening.job_id,
            candidate_id=candidate_id,
            user_id=user.id,
        )

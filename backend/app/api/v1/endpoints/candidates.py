"""Candidate repository endpoints (architecture doc §16)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import skill_categories
from app.api.dependencies import current_user
from app.db.database import get_session
from app.db.models import (
    PIPELINE_ORDER,
    Application,
    Job,
    Screening,
    ScreeningResult,
    User,
)
from app.schemas import (
    BestMatchOut,
    JobMatchOut,
    CandidateDetailResponse,
    CandidateListItem,
    CandidateListResponse,
    CandidateOut,
    CandidateSummary,
    CandidateUpdateRequest,
    ProfileOut,
    ResumeOut,
)
from app.services import candidate_service

router = APIRouter(prefix="/candidates", tags=["candidates"])


@router.get("", response_model=CandidateListResponse)
def list_candidates(
    q: str | None = Query(None, description="Match on name or email"),
    skill: str | None = Query(
        None,
        description=(
            "Skill, case-insensitive. Resolved through the skill taxonomy as well as "
            "the parsed profile, so 'React' also matches a resume that said 'React.js'."
        ),
    ),
    location: str | None = Query(
        None,
        description=(
            "Where the candidate is, matched as a substring — 'Chennai' finds "
            "'Chennai, India'. Candidates with no location recorded are excluded."
        ),
    ),
    min_years: float | None = Query(None, ge=0, le=60),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> CandidateListResponse:
    candidates, total = candidate_service.list_candidates(
        session,
        user.id,
        query=q,
        skill=skill,
        location=location,
        min_years=min_years,
        limit=limit,
        offset=offset,
    )

    # Best score and furthest stage, fetched for the page in two queries rather
    # than per row — the alternative is 2N queries for a 50-row page.
    page_ids = [candidate.id for candidate in candidates]
    best_scores: dict[uuid.UUID, float] = {}
    stages: dict[uuid.UUID, str] = {}
    if page_ids:
        best_scores = {
            row[0]: float(row[1])
            for row in session.execute(
                select(
                    ScreeningResult.candidate_id,
                    func.max(ScreeningResult.composite_score),
                )
                .join(Screening, Screening.id == ScreeningResult.screening_id)
                .join(Job, Job.id == Screening.job_id)
                .where(Job.owner_id == user.id, ScreeningResult.candidate_id.in_(page_ids))
                .group_by(ScreeningResult.candidate_id)
            ).all()
        }
        # Furthest stage reached, ordered by the pipeline rather than alphabetically.
        rank = {stage.value: index for index, stage in enumerate(PIPELINE_ORDER)}
        for cid, stage in session.execute(
            select(Application.candidate_id, Application.stage)
            .join(Job, Job.id == Application.job_id)
            .where(Job.owner_id == user.id, Application.candidate_id.in_(page_ids))
        ).all():
            current = stages.get(cid)
            if current is None or rank.get(stage, -1) > rank.get(current, -1):
                stages[cid] = stage

    items = []
    for candidate in candidates:
        profile = candidate.latest_profile
        latest_resume = candidate.resumes[0] if candidate.resumes else None
        item = CandidateListItem.model_validate(candidate)
        item.current_title = profile.current_title if profile else None
        item.total_years_experience = float(profile.total_years_experience) if profile else 0.0
        item.skills = list(profile.skills) if profile else []
        item.primary_role = profile.primary_role if profile else None
        item.resume_status = latest_resume.status if latest_resume else None
        item.best_match_score = best_scores.get(candidate.id)
        item.stage = stages.get(candidate.id)
        items.append(item)

    return CandidateListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        summary=CandidateSummary(**candidate_service.pool_summary(session, user.id)),
    )


@router.get("/{candidate_id}", response_model=CandidateDetailResponse)
def get_candidate(
    candidate_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> CandidateDetailResponse:
    candidate = candidate_service.get_candidate(session, candidate_id, user.id)
    if candidate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found.")

    return _detail(session, candidate, user.id)


def _detail(session: Session, candidate, owner_id: uuid.UUID) -> CandidateDetailResponse:
    profile = candidate.latest_profile
    match = candidate_service.best_match(session, candidate.id, owner_id)

    applications = [
        {
            "id": str(row[0]),
            "job_id": str(row[1]),
            "job_title": row[2],
            "stage": row[3],
            "created_at": row[4].isoformat(),
        }
        for row in session.execute(
            select(
                Application.id,
                Job.id,
                Job.title,
                Application.stage,
                Application.created_at,
            )
            .join(Job, Job.id == Application.job_id)
            .where(Application.candidate_id == candidate.id, Job.owner_id == owner_id)
            .order_by(Application.updated_at.desc())
        ).all()
    ]

    job_matches, unscored = candidate_service.top_job_matches(
        session, candidate.id, owner_id, limit=5
    )

    return CandidateDetailResponse(
        candidate=CandidateOut.model_validate(candidate),
        profile=ProfileOut.model_validate(profile) if profile else None,
        resumes=[ResumeOut.model_validate(resume) for resume in candidate.resumes],
        best_match=BestMatchOut(**match) if match else None,
        skill_groups=skill_categories.group(list(profile.skills or [])) if profile else {},
        applications=applications,
        job_matches=[JobMatchOut(**row) for row in job_matches],
        unscored_jobs=unscored,
    )


@router.patch("/{candidate_id}", response_model=CandidateDetailResponse)
def update_candidate(
    candidate_id: uuid.UUID,
    body: CandidateUpdateRequest,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> CandidateDetailResponse:
    """Edit recruiter-owned fields, or archive the candidate."""
    try:
        candidate = candidate_service.update_candidate(
            session, candidate_id, user.id, **body.model_dump(exclude_unset=True)
        )
    except candidate_service.CandidateError as error:
        # "Not found" and "not yours" answer identically on purpose.
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in str(error).lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(code, str(error)) from error

    # Reloaded so relationships reflect the commit.
    fresh = candidate_service.get_candidate(session, candidate.id, user.id)
    return _detail(session, fresh, user.id)

"""Dashboard: what mode the system is in and how big the repository is.

The mode line matters operationally — a recruiter needs to know whether a shortlist
came from Gemini or from the deterministic engine, and guessing from the output is
not good enough.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.ai.embeddings import get_embedder
from app.api.dependencies import current_user
from app.core.config import settings
from app.db.database import get_session
from app.db.models import User
from app.schemas import (
    ActivityItemOut,
    AttentionItemOut,
    DashboardResponse,
    DayActivityOut,
    DayCountOut,
    JobCardOut,
    JobOut,
    MatchedCandidateOut,
    MetricOut,
    OpenJobRowOut,
    RecentCandidateOut,
    ReviewCandidateOut,
    RoleSliceOut,
)
from app.services import (
    analytics_service,
    application_service,
    candidate_service,
    job_service,
)

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(
    window_days: int = Query(7, ge=1, le=365),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> DashboardResponse:
    return DashboardResponse(
        mode=settings.mode,
        embedding_model=get_embedder().model,
        weights=settings.weights.as_dict(),
        funnel_limits={
            "retrieval_limit": settings.retrieval_limit,
            "rerank_limit": settings.rerank_limit,
            "shortlist_size": settings.shortlist_size,
        },
        stats=candidate_service.repository_stats(session, user.id),
        recent_jobs=[
            JobOut.model_validate(job) for job in job_service.list_jobs(session, user.id, limit=5)
        ],
        metrics=[
            MetricOut(**vars(metric))
            for metric in analytics_service.headline_metrics(session, user.id)
        ],
        candidates_by_role=[
            RoleSliceOut(**vars(slice_))
            for slice_ in analytics_service.candidates_by_role(session, user.id)
        ],
        candidates_added=[
            DayCountOut(**vars(point))
            for point in analytics_service.candidates_added(session, user.id)
        ],
        job_cards=[
            JobCardOut(**vars(card)) for card in analytics_service.recent_jobs(session, user.id)
        ],
        recent_candidates=[
            RecentCandidateOut(**vars(row))
            for row in analytics_service.recent_candidates(session, user.id)
        ],
        top_matched=[
            MatchedCandidateOut(**vars(row))
            for row in analytics_service.top_matched(session, user.id)
        ],
        attention=[
            AttentionItemOut(**vars(item))
            for item in analytics_service.attention_items(session, user.id)
        ],
        pipeline=application_service.pipeline_counts(session, user.id),
        activity=[
            ActivityItemOut(**vars(event))
            for event in analytics_service.activity_feed(session, user.id)
        ],
        review_queue=[
            ReviewCandidateOut(**vars(row))
            for row in analytics_service.review_queue(session, user.id)
        ],
        open_jobs=[
            OpenJobRowOut(**vars(row))
            for row in analytics_service.open_jobs(session, user.id)
        ],
        pipeline_deltas=analytics_service.pipeline_deltas(
            session, user.id, days=window_days
        ),
        window_days=window_days,
        weekly_activity=[
            DayActivityOut(**vars(point))
            for point in analytics_service.weekly_activity(session, user.id)
        ],
    )


@router.get("/activity", response_model=list[ActivityItemOut])
def activity(
    limit: int = Query(8, ge=1, le=50),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> list[ActivityItemOut]:
    """The notification feed on its own.

    Split out from ``/dashboard`` because the bell lives in the shell and is
    therefore rendered on every page — pulling the whole dashboard payload for it
    would run the attention queue, the pipeline and both tables on every route.
    """
    return [
        ActivityItemOut(**vars(event))
        for event in analytics_service.activity_feed(session, user.id, limit=limit)
    ]

"""Search sessions (architecture doc §13, §14).

Three endpoints, matching the design document:

    POST /search                  run matching and open a session
    GET  /search/{session_id}     the page already shown
    GET  /search/{session_id}/next   the next page — "show me other profiles"

``recruiter_id`` is always the authenticated user. A session id belonging to another
recruiter returns 404 rather than 403: distinguishing the two lets a caller confirm
that an id exists, which is the enumeration hole §6 calls out.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import current_user
from app.db.database import get_session
from app.db.models import User
from app.schemas import SearchCandidateOut, SearchCreateRequest, SearchPageResponse
from app.services import job_service, matching_service, search_service
from app.services.search_service import SearchPage, SearchSessionNotFound

router = APIRouter(prefix="/search", tags=["search"])


def _primary_role(candidate) -> str | None:
    """Role from the candidate's current profile, if one has been classified."""
    profile = getattr(candidate, "latest_profile", None) if candidate else None
    return profile.primary_role if profile else None


def _to_response(page: SearchPage) -> SearchPageResponse:
    return SearchPageResponse(
        session_id=page.session.id,
        job_id=page.session.job_id,
        screening_id=page.session.screening_id,
        status=page.session.status,
        page_size=page.session.page_size,
        total=page.total,
        remaining=page.remaining,
        relaxations=list(page.session.relaxations or []),
        candidates=[
            SearchCandidateOut(
                rank=row.rank,
                candidate_id=row.candidate_id,
                name=(
                    page.candidates[row.candidate_id].full_name
                    if row.candidate_id in page.candidates
                    else "Unknown"
                ),
                score=float(row.score),
                primary_role=_primary_role(page.candidates.get(row.candidate_id)),
                shown_at=row.shown_at,
            )
            for row in page.results
        ],
    )


@router.post("", response_model=SearchPageResponse, status_code=status.HTTP_201_CREATED)
def run_search(
    payload: SearchCreateRequest,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> SearchPageResponse:
    """Run matching for a job and return the first page of a new session."""
    job = job_service.get_job(session, payload.job_id, user.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")

    if job.requirement is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Job has no parsed requirements; re-create the job."
        )

    # Run inline rather than enqueueing: a search is a request/response interaction,
    # and the caller needs the first page back, not a job id to poll.
    screening = matching_service.create_screening(session, job=job)
    matching_service.run_screening(session, screening.id)
    session.refresh(screening)

    search_session = search_service.create_session(
        session,
        recruiter_id=user.id,
        screening=screening,
        page_size=payload.page_size,
        applied_filters=dict(job.requirement.hard_filters) if job.requirement else {},
    )
    return _to_response(search_service.next_page(session, search_session))


@router.get("/{session_id}", response_model=SearchPageResponse)
def get_search(
    session_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> SearchPageResponse:
    """The candidates already shown for this session. Does not advance the cursor."""
    try:
        search_session = search_service.get_session(session, session_id, user.id)
    except SearchSessionNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Search session not found.") from exc
    return _to_response(search_service.current_page(session, search_session))


@router.get("/{session_id}/next", response_model=SearchPageResponse)
def next_profiles(
    session_id: uuid.UUID,
    limit: int | None = Query(default=None, ge=1, le=50),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> SearchPageResponse:
    """Show me other profiles.

    Returns an empty candidate list once the ranking is exhausted — that is a normal
    answer, not an error.
    """
    try:
        search_session = search_service.get_session(session, session_id, user.id)
    except SearchSessionNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Search session not found.") from exc
    return _to_response(search_service.next_page(session, search_session, limit=limit))

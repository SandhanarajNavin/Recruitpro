"""Interview endpoints (architecture doc §11).

Ownership is enforced by the join in every query, so another recruiter's interview
answers 404 rather than 403 — the difference between "not yours" and "not there"
is itself information.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import current_user
from app.db.database import get_session
from app.db.models import User
from app.schemas import (
    InterviewCreateRequest,
    InterviewListResponse,
    InterviewOut,
    InterviewUpdateRequest,
)
from app.services import interview_service
from app.services.interview_service import InterviewError

router = APIRouter(prefix="/interviews", tags=["interviews"])


def _fail(error: InterviewError) -> HTTPException:
    code = (
        status.HTTP_404_NOT_FOUND
        if "not found" in str(error).lower()
        else status.HTTP_400_BAD_REQUEST
    )
    return HTTPException(code, str(error))


@router.get("", response_model=InterviewListResponse)
def list_interviews(
    window: str = Query("upcoming", pattern="^(week|upcoming|past|all)$"),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> InterviewListResponse:
    try:
        rows = interview_service.list_interviews(session, user.id, window=window)
    except InterviewError as error:
        raise _fail(error) from error

    return InterviewListResponse(
        items=[InterviewOut(**vars(row)) for row in rows],
        counts=interview_service.counts(session, user.id),
    )


@router.post("", response_model=InterviewOut, status_code=status.HTTP_201_CREATED)
def schedule_interview(
    body: InterviewCreateRequest,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> InterviewOut:
    try:
        row = interview_service.schedule(
            session,
            user.id,
            application_id=body.application_id,
            scheduled_at=body.scheduled_at,
            kind=body.kind,
            interviewer=body.interviewer,
            advance_stage=body.advance_stage,
        )
    except InterviewError as error:
        raise _fail(error) from error
    return InterviewOut(**vars(row))


@router.patch("/{interview_id}", response_model=InterviewOut)
def update_interview(
    interview_id: uuid.UUID,
    body: InterviewUpdateRequest,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> InterviewOut:
    """Reschedule, or record how it went."""
    try:
        row = interview_service.record(
            session,
            user.id,
            interview_id,
            **body.model_dump(exclude_unset=True),
        )
    except InterviewError as error:
        raise _fail(error) from error
    return InterviewOut(**vars(row))


@router.delete("/{interview_id}", response_model=InterviewOut)
def cancel_interview(
    interview_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> InterviewOut:
    """Cancels rather than deletes — a booked-then-dropped slot is part of the record."""
    try:
        row = interview_service.cancel(session, user.id, interview_id)
    except InterviewError as error:
        raise _fail(error) from error
    return InterviewOut(**vars(row))

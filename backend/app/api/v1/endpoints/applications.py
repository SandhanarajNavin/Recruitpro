"""Hiring pipeline endpoints.

    POST  /applications              open one (or return the existing pair)
    GET   /applications              list, filterable by job and stage
    GET   /applications/pipeline     stage counts for the funnel strip
    GET   /applications/{id}         one application with its full history
    PATCH /applications/{id}         move it to another stage

An illegal stage move returns 409, not 400: the request is well-formed, the
*current state* is what makes it impossible, and the message says what is allowed
from here so the caller can correct itself.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import current_user
from app.db.database import get_session
from app.db.models import Application, ApplicationStage, Candidate, Job, User
from app.schemas import (
    ApplicationCreateRequest,
    ApplicationDetail,
    ApplicationEventOut,
    ApplicationMoveRequest,
    ApplicationOut,
    ApplicationRow,
    PipelineOut,
)
from app.services import application_service
from app.services.application_service import ApplicationError

router = APIRouter(prefix="/applications", tags=["pipeline"])


def _stage(value: str) -> ApplicationStage:
    try:
        return ApplicationStage(value)
    except ValueError as exc:
        allowed = ", ".join(s.value for s in ApplicationStage)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unknown stage {value!r}. Expected one of: {allowed}.",
        ) from exc


@router.post("", response_model=ApplicationOut, status_code=status.HTTP_201_CREATED)
def open_application(
    body: ApplicationCreateRequest,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ApplicationOut:
    """Start an application. Idempotent for a candidate/job pair."""
    try:
        application = application_service.open_application(
            session,
            owner_id=user.id,
            candidate_id=body.candidate_id,
            job_id=body.job_id,
            stage=_stage(body.stage) if body.stage else ApplicationStage.APPLIED,
            screening_result_id=body.screening_result_id,
            actor_id=user.id,
        )
    except ApplicationError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return ApplicationOut.model_validate(application)


@router.get("", response_model=list[ApplicationRow])
def list_applications(
    job_id: uuid.UUID | None = Query(None),
    stage: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> list[ApplicationRow]:
    statement = (
        select(
            Application.id,
            Application.candidate_id,
            Candidate.full_name,
            Application.job_id,
            Job.title,
            Application.stage,
            Application.match_score_at_entry,
            Application.created_at,
        )
        .join(Job, Job.id == Application.job_id)
        .join(Candidate, Candidate.id == Application.candidate_id)
        .where(Job.owner_id == user.id)
        .order_by(Application.created_at.desc())
        .limit(limit)
    )
    if job_id is not None:
        statement = statement.where(Application.job_id == job_id)
    if stage is not None:
        statement = statement.where(Application.stage == _stage(stage).value)

    return [
        ApplicationRow(
            id=row[0],
            candidate_id=row[1],
            candidate_name=row[2],
            job_id=row[3],
            job_title=row[4],
            stage=row[5],
            match_score_at_entry=float(row[6]) if row[6] is not None else None,
            created_at=row[7],
        )
        for row in session.execute(statement).all()
    ]


@router.get("/pipeline", response_model=PipelineOut)
def pipeline(
    job_id: uuid.UUID | None = Query(None, description="Limit to one role."),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> PipelineOut:
    stages = application_service.pipeline_counts(session, user.id, job_id=job_id)
    return PipelineOut(stages=stages, total=sum(stages.values()))


@router.get("/{application_id}", response_model=ApplicationDetail)
def get_application(
    application_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ApplicationDetail:
    try:
        application = application_service.get(session, application_id, user.id)
    except ApplicationError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    candidate = session.get(Candidate, application.candidate_id)
    job = session.get(Job, application.job_id)
    return ApplicationDetail(
        application=ApplicationOut.model_validate(application),
        candidate_name=candidate.full_name if candidate else "Unknown",
        job_title=job.title if job else "Unknown",
        events=[ApplicationEventOut.model_validate(e) for e in application.events],
    )


@router.patch("/{application_id}", response_model=ApplicationOut)
def move_application(
    application_id: uuid.UUID,
    body: ApplicationMoveRequest,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ApplicationOut:
    try:
        application = application_service.move(
            session,
            owner_id=user.id,
            application_id=application_id,
            to_stage=_stage(body.to_stage),
            actor_id=user.id,
            note=body.note,
        )
    except ApplicationError as exc:
        message = str(exc)
        # "No such application" is a 404; an illegal move is a state conflict.
        if message.startswith("No such"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, message) from exc
        raise HTTPException(status.HTTP_409_CONFLICT, message) from exc
    return ApplicationOut.model_validate(application)

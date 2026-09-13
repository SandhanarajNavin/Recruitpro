"""Moving candidates through the hiring pipeline.

The stage machine lives here rather than in the endpoint because it is a property of
the domain, not of HTTP: the same rules must hold whether a move comes from the UI,
the assistant, or a future bulk action.

Two rules are enforced rather than suggested:

- **Transitions are explicit.** A candidate cannot skip from applied to offer, and
  cannot move backwards except by an explicit reopen. Silent illegal moves make
  pipeline reporting meaningless — a funnel only means something if everyone in it
  passed through the stages above.
- **Every move is recorded.** ``application_events`` keeps from/to/actor/when
  forever. The current stage answers "where are they"; only the events answer "how
  long has this taken" or "who moved them".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import (
    PIPELINE_ORDER,
    Application,
    ApplicationEvent,
    ApplicationStage,
    Candidate,
    Job,
    ScreeningResult,
)
from app.services import audit_service

logger = get_logger(__name__)

#: What may follow what. Rejection is reachable from any active stage; hiring only
#: from offer, because a hire that skipped the offer stage is a record-keeping error.
ALLOWED: dict[ApplicationStage, set[ApplicationStage]] = {
    ApplicationStage.APPLIED: {ApplicationStage.SCREENING, ApplicationStage.REJECTED},
    ApplicationStage.SCREENING: {
        ApplicationStage.SHORTLISTED,
        ApplicationStage.REJECTED,
    },
    ApplicationStage.SHORTLISTED: {
        ApplicationStage.INTERVIEW,
        ApplicationStage.REJECTED,
    },
    ApplicationStage.INTERVIEW: {ApplicationStage.OFFER, ApplicationStage.REJECTED},
    ApplicationStage.OFFER: {ApplicationStage.HIRED, ApplicationStage.REJECTED},
    ApplicationStage.HIRED: set(),
    # A rejection can be undone — recruiters change their minds, and the alternative
    # is a duplicate application that breaks the unique constraint.
    ApplicationStage.REJECTED: {ApplicationStage.SHORTLISTED},
}

TERMINAL = {ApplicationStage.HIRED, ApplicationStage.REJECTED}


class ApplicationError(ValueError):
    """An illegal move, or an application that does not belong to this recruiter."""


def _owned_job(session: Session, job_id: uuid.UUID, owner_id: uuid.UUID) -> Job:
    job = session.execute(
        select(Job).where(Job.id == job_id, Job.owner_id == owner_id)
    ).scalars().first()
    if job is None:
        raise ApplicationError("No such job for this recruiter.")
    return job


def get(
    session: Session, application_id: uuid.UUID, owner_id: uuid.UUID
) -> Application:
    """Ownership is a join, not a check after the fact."""
    found = session.execute(
        select(Application)
        .join(Job, Job.id == Application.job_id)
        .where(Application.id == application_id, Job.owner_id == owner_id)
    ).scalars().first()
    if found is None:
        raise ApplicationError("No such application for this recruiter.")
    return found


def open_application(
    session: Session,
    *,
    owner_id: uuid.UUID,
    candidate_id: uuid.UUID,
    job_id: uuid.UUID,
    stage: ApplicationStage = ApplicationStage.APPLIED,
    screening_result_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
) -> Application:
    """Start an application, or return the existing one for this pair.

    Idempotent because the natural trigger — "shortlist this candidate" — is a
    button a recruiter will press twice.
    """
    _owned_job(session, job_id, owner_id)
    candidate = session.execute(
        select(Candidate).where(Candidate.id == candidate_id, Candidate.owner_id == owner_id)
    ).scalars().first()
    if candidate is None:
        raise ApplicationError("No such candidate for this recruiter.")

    existing = session.execute(
        select(Application).where(
            Application.candidate_id == candidate_id, Application.job_id == job_id
        )
    ).scalars().first()
    if existing is not None:
        return existing

    score = None
    if screening_result_id is not None:
        score = session.execute(
            select(ScreeningResult.composite_score).where(
                ScreeningResult.id == screening_result_id
            )
        ).scalar()

    application = Application(
        candidate_id=candidate_id,
        job_id=job_id,
        stage=stage.value,
        origin_screening_result_id=screening_result_id,
        match_score_at_entry=score,
    )
    session.add(application)
    session.flush()

    session.add(
        ApplicationEvent(
            application_id=application.id,
            from_stage=None,
            to_stage=stage.value,
            actor_id=actor_id,
        )
    )
    session.commit()

    audit_service.record(
        session,
        "application.opened",
        recruiter_id=owner_id,
        resource_type="application",
        resource_id=application.id,
        candidate_id=candidate_id,
        job_id=job_id,
        stage=stage.value,
    )
    return application


def move(
    session: Session,
    *,
    owner_id: uuid.UUID,
    application_id: uuid.UUID,
    to_stage: ApplicationStage,
    actor_id: uuid.UUID | None = None,
    note: str | None = None,
) -> Application:
    """Advance (or reject) an application, enforcing the stage machine."""
    application = get(session, application_id, owner_id)
    current = ApplicationStage(application.stage)

    if to_stage == current:
        return application
    if to_stage not in ALLOWED[current]:
        allowed = ", ".join(sorted(s.value for s in ALLOWED[current])) or "nothing"
        raise ApplicationError(
            f"Cannot move from {current.value} to {to_stage.value}. "
            f"Allowed from {current.value}: {allowed}."
        )

    application.stage = to_stage.value
    if to_stage == ApplicationStage.REJECTED and note:
        application.rejection_reason = note
    application.closed_at = datetime.now(UTC) if to_stage in TERMINAL else None

    session.add(
        ApplicationEvent(
            application_id=application.id,
            from_stage=current.value,
            to_stage=to_stage.value,
            actor_id=actor_id,
            note=note,
        )
    )
    session.commit()

    audit_service.record(
        session,
        "application.moved",
        recruiter_id=owner_id,
        resource_type="application",
        resource_id=application.id,
        from_stage=current.value,
        to_stage=to_stage.value,
    )
    return application


def pipeline_counts(
    session: Session, owner_id: uuid.UUID, job_id: uuid.UUID | None = None
) -> dict[str, int]:
    """Candidates at each stage, for the pipeline strip.

    Every stage appears even at zero — a funnel with gaps where nobody stands reads
    as missing data rather than as an empty stage.
    """
    statement = (
        select(Application.stage, func.count(Application.id))
        .join(Job, Job.id == Application.job_id)
        .where(Job.owner_id == owner_id)
        .group_by(Application.stage)
    )
    if job_id is not None:
        statement = statement.where(Application.job_id == job_id)

    counted = dict(session.execute(statement).all())
    return {stage.value: int(counted.get(stage.value, 0)) for stage in PIPELINE_ORDER}

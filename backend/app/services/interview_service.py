"""Scheduling and recording interviews (architecture doc §11).

An interview hangs off an application, not off a candidate: the same person can be
interviewed for two roles, and the notes and outcome belong to one of them. That is
also what makes ownership checkable — the application knows its job, and the job
knows its owner.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.logging import audit, get_logger
from app.db.models import (
    Application,
    ApplicationStage,
    Candidate,
    Interview,
    Job,
)
from app.db.models.application import PIPELINE_ORDER, InterviewOutcome
from app.services import application_service
from app.services.application_service import ApplicationError

logger = get_logger(__name__)

#: Outcomes that still expect the interview to happen.
OPEN_OUTCOMES = (InterviewOutcome.SCHEDULED.value,)


class InterviewError(ValueError):
    """An interview operation the domain refuses."""


@dataclass
class InterviewRow:
    """One interview, with enough context to be actionable in a list."""

    id: uuid.UUID
    application_id: uuid.UUID
    candidate_id: uuid.UUID
    candidate_name: str
    job_id: uuid.UUID
    job_title: str
    scheduled_at: datetime
    kind: str | None
    interviewer: str | None
    outcome: str
    notes: str | None
    #: The application's current pipeline stage, which the outcome does not imply.
    stage: str


def _owned(owner_id: uuid.UUID) -> Select:
    """Base query joining an interview to the rows that prove who owns it."""
    return (
        select(
            Interview.id,
            Interview.application_id,
            Candidate.id,
            Candidate.full_name,
            Job.id,
            Job.title,
            Interview.scheduled_at,
            Interview.kind,
            Interview.interviewer,
            Interview.outcome,
            Interview.notes,
            Application.stage,
        )
        .join(Application, Application.id == Interview.application_id)
        .join(Job, Job.id == Application.job_id)
        .join(Candidate, Candidate.id == Application.candidate_id)
        .where(Job.owner_id == owner_id)
    )


def _row(values) -> InterviewRow:
    return InterviewRow(
        id=values[0],
        application_id=values[1],
        candidate_id=values[2],
        candidate_name=values[3],
        job_id=values[4],
        job_title=values[5],
        scheduled_at=values[6],
        kind=values[7],
        interviewer=values[8],
        outcome=values[9],
        notes=values[10],
        stage=values[11],
    )


def list_interviews(
    session: Session,
    owner_id: uuid.UUID,
    *,
    window: str = "upcoming",
    limit: int = 100,
) -> list[InterviewRow]:
    """Interviews for this recruiter, in the order the window implies.

    ``upcoming`` and ``week`` sort ascending — the next one first, which is what a
    recruiter opening the page wants. ``past`` sorts descending, because the most
    recent is the one being written up.
    """
    now = datetime.now(UTC)
    statement = _owned(owner_id)

    if window == "week":
        # Monday of the current week through the following Monday.
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        statement = statement.where(
            Interview.scheduled_at >= start,
            Interview.scheduled_at < start + timedelta(days=7),
        )
        statement = statement.order_by(Interview.scheduled_at.asc())
    elif window == "past":
        statement = statement.where(Interview.scheduled_at < now)
        statement = statement.order_by(Interview.scheduled_at.desc())
    elif window == "upcoming":
        statement = statement.where(Interview.scheduled_at >= now)
        statement = statement.order_by(Interview.scheduled_at.asc())
    elif window == "all":
        statement = statement.order_by(Interview.scheduled_at.desc())
    else:
        raise InterviewError(
            f"Unknown window {window!r}. Expected week, upcoming, past or all."
        )

    return [_row(values) for values in session.execute(statement.limit(limit)).all()]


def counts(session: Session, owner_id: uuid.UUID) -> dict[str, int]:
    """Tab counts, so each tab shows its size before it is opened."""
    return {
        window: len(list_interviews(session, owner_id, window=window, limit=1000))
        for window in ("week", "upcoming", "past")
    }


def schedule(
    session: Session,
    owner_id: uuid.UUID,
    *,
    application_id: uuid.UUID,
    scheduled_at: datetime,
    kind: str | None = None,
    interviewer: str | None = None,
    advance_stage: bool = True,
) -> InterviewRow:
    """Book an interview against an application the caller owns.

    With ``advance_stage`` the application also moves to the interview stage, which
    is what makes the booking show up in the pipeline. It is a flag rather than an
    unconditional side effect because the two are genuinely separate facts — a slot
    can be held before the decision is made — but the common case is that booking
    an interview *is* the decision, so it defaults on.

    The move is best-effort: an application that cannot legally reach the interview
    stage (already at offer, say) still gets its interview booked rather than the
    whole call failing on a stage machine technicality.
    """
    application = session.execute(
        select(Application)
        .join(Job, Job.id == Application.job_id)
        .where(Application.id == application_id, Job.owner_id == owner_id)
    ).scalar_one_or_none()
    if application is None:
        raise InterviewError("Application not found.")
    if application.stage == ApplicationStage.REJECTED.value:
        raise InterviewError("That application is rejected — reopen it before booking.")

    interview = Interview(
        application_id=application_id,
        scheduled_at=scheduled_at,
        kind=(kind or "").strip() or None,
        interviewer=(interviewer or "").strip() or None,
        outcome=InterviewOutcome.SCHEDULED.value,
    )
    session.add(interview)
    session.commit()

    moved = False
    if advance_stage:
        moved = advance_to_interview(session, owner_id, application)

    audit(
        "interview.scheduled",
        interview_id=str(interview.id),
        application_id=str(application_id),
        advanced=moved,
    )
    return get(session, owner_id, interview.id)


def advance_to_interview(
    session: Session, owner_id: uuid.UUID, application: Application
) -> bool:
    """Walk an application forward to the interview stage, one legal step at a time.

    The stage machine only permits adjacent moves, so an application still at
    ``applied`` cannot jump straight to ``interview`` — it has to pass through
    screening and shortlisted. Walking it records an event for each, which is the
    truthful history: you cannot reach an interview without having been shortlisted
    for it.

    Returns whether the application ended up at the interview stage. A candidate
    already past it (at offer, say) is left alone rather than moved backwards.
    """
    target = ApplicationStage.INTERVIEW
    order = list(PIPELINE_ORDER)
    try:
        current = ApplicationStage(application.stage)
    except ValueError:
        return False

    if current == target:
        return True
    if current not in order or order.index(current) > order.index(target):
        # Already beyond interview, or off the main line entirely.
        return False

    while order.index(ApplicationStage(application.stage)) < order.index(target):
        nxt = order[order.index(ApplicationStage(application.stage)) + 1]
        try:
            application_service.move(
                session,
                owner_id=owner_id,
                application_id=application.id,
                to_stage=nxt,
                note="Interview scheduled.",
            )
        except ApplicationError:
            # The interview is already saved and is the point of the call; a stage
            # that will not move is worth logging, not worth losing the booking to.
            logger.info(
                "Interview booked but stage not advanced",
                extra={
                    "application_id": str(application.id),
                    "stage": application.stage,
                },
            )
            return False
        session.refresh(application)

    return True


def get(session: Session, owner_id: uuid.UUID, interview_id: uuid.UUID) -> InterviewRow:
    values = session.execute(
        _owned(owner_id).where(Interview.id == interview_id)
    ).first()
    if values is None:
        raise InterviewError("Interview not found.")
    return _row(values)


def record(
    session: Session,
    owner_id: uuid.UUID,
    interview_id: uuid.UUID,
    *,
    outcome: str | None = None,
    notes: str | None = None,
    scheduled_at: datetime | None = None,
) -> InterviewRow:
    """Update an interview: reschedule it, or record how it went."""
    interview = session.execute(
        select(Interview)
        .join(Application, Application.id == Interview.application_id)
        .join(Job, Job.id == Application.job_id)
        .where(Interview.id == interview_id, Job.owner_id == owner_id)
    ).scalar_one_or_none()
    if interview is None:
        raise InterviewError("Interview not found.")

    if outcome is not None:
        valid = {member.value for member in InterviewOutcome}
        if outcome not in valid:
            raise InterviewError(
                f"Unknown outcome {outcome!r}. Expected one of {sorted(valid)}."
            )
        interview.outcome = outcome
    if notes is not None:
        interview.notes = notes.strip() or None
    if scheduled_at is not None:
        interview.scheduled_at = scheduled_at

    session.commit()
    audit("interview.updated", interview_id=str(interview_id), outcome=interview.outcome)
    return get(session, owner_id, interview_id)


def cancel(session: Session, owner_id: uuid.UUID, interview_id: uuid.UUID) -> InterviewRow:
    """Mark an interview cancelled rather than deleting it.

    The fact that a slot was booked and dropped is part of the record — deleting it
    would make the pipeline history describe something that did not happen.
    """
    return record(
        session, owner_id, interview_id, outcome=InterviewOutcome.CANCELLED.value
    )

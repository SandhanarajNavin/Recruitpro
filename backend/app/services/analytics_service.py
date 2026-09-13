"""Dashboard aggregates.

Everything here is derived from rows the recruiter already owns — there are no
stored counters to drift out of sync, and every query is scoped by owner id.

Kept apart from ``candidate_service`` because these are read-only reporting queries
with a different shape: they group and window rather than fetch a working set, and
they are allowed to be approximate at the edges (a month boundary) in a way the
candidate queries are not.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Application,
    ApplicationEvent,
    Candidate,
    CandidateProfile,
    Interview,
    Job,
    Resume,
    Screening,
    ScreeningResult,
)
from app.db.models.application import PIPELINE_ORDER
from app.db.models.candidate import CandidateStatus
from app.db.models.job import JobStatus
from app.db.models.screening import Recommendation
from app.services.scoring import BAND_STRONG_HIRE as STRONG_MATCH_SCORE

#: How many days the "candidates added" series covers.
TREND_DAYS = 30
#: Roles beyond this are folded into an "Others" slice, matching the donut design.
MAX_ROLE_SLICES = 4


@dataclass
class Metric:
    key: str
    label: str
    value: int
    #: Percent change against the preceding window. None when the previous window
    #: was empty — "up 100%" from zero is noise, not information.
    delta_pct: float | None
    #: Line of context under the number. Not always a delta: some cards explain
    #: where the figure comes from rather than how it moved.
    hint: str | None = None
    #: neutral | up | down | warn — drives colour only.
    tone: str = "neutral"


@dataclass
class RoleSlice:
    role: str
    count: int
    share: float


@dataclass
class DayCount:
    day: date
    count: int


@dataclass
class JobCard:
    id: uuid.UUID
    title: str
    created_at: datetime
    match_count: int
    status: str


@dataclass
class MatchedCandidate:
    candidate_id: uuid.UUID
    name: str
    role: str | None
    score: float
    years: float


def _window() -> tuple[datetime, datetime]:
    """Current and preceding comparison windows, both TREND_DAYS long."""
    now = datetime.now(UTC)
    start = now - timedelta(days=TREND_DAYS)
    previous_start = start - timedelta(days=TREND_DAYS)
    return start, previous_start


def _delta(current: int, previous: int) -> float | None:
    if previous <= 0:
        return None
    return round(((current - previous) / previous) * 100, 1)


def _counted(session: Session, statement: Select) -> int:
    return int(session.execute(statement).scalar() or 0)


def _metric(
    session: Session,
    *,
    key: str,
    label: str,
    total: Select,
    created_column,
    scoped: Select,
) -> Metric:
    start, previous_start = _window()
    current = _counted(session, scoped.where(created_column >= start))
    previous = _counted(
        session,
        scoped.where(created_column >= previous_start, created_column < start),
    )
    return Metric(
        key=key,
        label=label,
        value=_counted(session, total),
        delta_pct=_delta(current, previous),
    )


def headline_metrics(session: Session, owner_id: uuid.UUID) -> list[Metric]:
    """The four cards across the top of the dashboard.

    Each one answers "what should I do next", not "how big is the database": how
    many people are waiting on me, how many roles are live, who the pipeline thinks
    is worth interviewing, and what is actually booked this week.
    """
    now = datetime.now(UTC)
    active = (
        Candidate.owner_id == owner_id,
        Candidate.status == CandidateStatus.ACTIVE.value,
    )

    # Candidates nobody has opened an application for yet.
    with_application = (
        select(Application.candidate_id)
        .join(Job, Job.id == Application.job_id)
        .where(Job.owner_id == owner_id)
        .distinct()
        .scalar_subquery()
    )
    to_review = _counted(
        session,
        select(func.count(Candidate.id)).where(*active, Candidate.id.notin_(with_application)),
    )
    new_today = _counted(
        session,
        select(func.count(Candidate.id)).where(
            *active, Candidate.created_at >= now - timedelta(days=1)
        ),
    )

    open_job_count = _counted(
        session,
        select(func.count(Job.id)).where(
            Job.owner_id == owner_id, Job.status == JobStatus.OPEN.value
        ),
    )
    needing_attention = len(attention_items(session, owner_id, limit=100))

    recommended = _counted(
        session,
        select(func.count(func.distinct(ScreeningResult.candidate_id)))
        .join(Screening, Screening.id == ScreeningResult.screening_id)
        .join(Job, Job.id == Screening.job_id)
        .where(
            Job.owner_id == owner_id,
            func.coalesce(
                ScreeningResult.override_recommendation, ScreeningResult.recommendation
            ).in_([Recommendation.INTERVIEW.value, Recommendation.STRONG_HIRE.value]),
        ),
    )

    def _interviews(since: datetime, until: datetime) -> int:
        return _counted(
            session,
            select(func.count(Interview.id))
            .join(Application, Application.id == Interview.application_id)
            .join(Job, Job.id == Application.job_id)
            .where(
                Job.owner_id == owner_id,
                Interview.scheduled_at >= since,
                Interview.scheduled_at < until,
            ),
        )

    week_start = now - timedelta(days=now.weekday(), hours=now.hour, minutes=now.minute)
    week_start = week_start.replace(second=0, microsecond=0)
    this_week = _interviews(week_start, week_start + timedelta(days=7))
    last_week = _interviews(week_start - timedelta(days=7), week_start)
    change = this_week - last_week

    return [
        Metric(
            key="to_review",
            label="Candidates to review",
            value=to_review,
            delta_pct=None,
            hint=f"{new_today} new today" if new_today else "nothing new today",
            tone="up" if new_today else "neutral",
        ),
        Metric(
            key="jobs",
            label="Active jobs",
            value=open_job_count,
            delta_pct=None,
            hint=(
                f"{needing_attention} need attention"
                if needing_attention
                else "all up to date"
            ),
            tone="warn" if needing_attention else "neutral",
        ),
        Metric(
            key="interview",
            label="Recommended to interview",
            value=recommended,
            delta_pct=None,
            hint="Based on latest screening",
        ),
        Metric(
            key="interviews_week",
            label="Interviews this week",
            value=this_week,
            delta_pct=None,
            # No interviews last week is not "unchanged" — say nothing rather than
            # imply a flat trend against an empty baseline.
            hint=(
                f"{change:+d} from last week"
                if last_week or this_week
                else "none scheduled"
            ),
            tone="up" if change > 0 else "down" if change < 0 else "neutral",
        ),
    ]


def candidates_by_role(session: Session, owner_id: uuid.UUID) -> list[RoleSlice]:
    """Distribution over ``primary_role`` from the latest profile per candidate.

    Unclassified profiles are counted as "Unclassified" rather than dropped —
    omitting them would make the slices sum to less than the headline count and
    quietly misrepresent the pool.
    """
    latest = (
        select(
            CandidateProfile.candidate_id.label("candidate_id"),
            func.max(CandidateProfile.version).label("version"),
        )
        .join(Candidate, Candidate.id == CandidateProfile.candidate_id)
        .where(Candidate.owner_id == owner_id, Candidate.status == CandidateStatus.ACTIVE.value)
        .group_by(CandidateProfile.candidate_id)
        .subquery()
    )

    rows = session.execute(
        select(CandidateProfile.primary_role, func.count(CandidateProfile.id))
        .join(
            latest,
            (CandidateProfile.candidate_id == latest.c.candidate_id)
            & (CandidateProfile.version == latest.c.version),
        )
        .group_by(CandidateProfile.primary_role)
    ).all()

    counted = [(role or "Unclassified", int(count)) for role, count in rows]
    total = sum(count for _role, count in counted)
    if total == 0:
        return []

    counted.sort(key=lambda item: item[1], reverse=True)
    head = counted[:MAX_ROLE_SLICES]
    tail_total = sum(count for _role, count in counted[MAX_ROLE_SLICES:])
    if tail_total:
        head.append(("Others", tail_total))

    return [
        RoleSlice(role=role, count=count, share=round(count / total * 100, 1))
        for role, count in head
    ]


def candidates_added(session: Session, owner_id: uuid.UUID) -> list[DayCount]:
    """Daily additions over the trend window, zero-filled.

    Zero-filling matters: a line chart that skips empty days compresses a quiet week
    into a single step and reads as steady growth.
    """
    start, _previous = _window()
    rows = session.execute(
        select(
            func.date(Candidate.created_at).label("day"),
            func.count(Candidate.id),
        )
        .where(Candidate.owner_id == owner_id, Candidate.created_at >= start)
        .group_by(func.date(Candidate.created_at))
    ).all()

    by_day = {row[0]: int(row[1]) for row in rows}
    first = start.date()
    series = []
    for offset in range(TREND_DAYS + 1):
        day = first + timedelta(days=offset)
        series.append(DayCount(day=day, count=by_day.get(day, 0)))
    return series


def recent_jobs(session: Session, owner_id: uuid.UUID, limit: int = 4) -> list[JobCard]:
    """Latest roles with how many candidates any screening has matched to them."""
    match_count = (
        select(func.count(func.distinct(ScreeningResult.candidate_id)))
        .join(Screening, Screening.id == ScreeningResult.screening_id)
        .where(Screening.job_id == Job.id)
        .correlate(Job)
        .scalar_subquery()
    )

    rows = session.execute(
        select(Job.id, Job.title, Job.created_at, Job.status, match_count.label("matches"))
        .where(Job.owner_id == owner_id)
        .order_by(Job.created_at.desc())
        .limit(limit)
    ).all()

    return [
        JobCard(
            id=row[0],
            title=row[1],
            created_at=row[2],
            status=row[3],
            match_count=int(row[4] or 0),
        )
        for row in rows
    ]


def top_matched(session: Session, owner_id: uuid.UUID, limit: int = 4) -> list[MatchedCandidate]:
    """Best-scoring candidates across every screening this recruiter has run.

    One row per candidate — their best score. Without the de-duplication a strong
    candidate screened against three roles would fill the whole panel.
    """
    best = (
        select(
            ScreeningResult.candidate_id.label("candidate_id"),
            func.max(ScreeningResult.composite_score).label("score"),
        )
        .join(Screening, Screening.id == ScreeningResult.screening_id)
        .join(Job, Job.id == Screening.job_id)
        .where(Job.owner_id == owner_id)
        .group_by(ScreeningResult.candidate_id)
        .subquery()
    )

    latest_profile = (
        select(
            CandidateProfile.candidate_id.label("candidate_id"),
            func.max(CandidateProfile.version).label("version"),
        )
        .group_by(CandidateProfile.candidate_id)
        .subquery()
    )

    rows = session.execute(
        select(
            Candidate.id,
            Candidate.full_name,
            CandidateProfile.primary_role,
            best.c.score,
            CandidateProfile.total_years_experience,
        )
        .join(best, best.c.candidate_id == Candidate.id)
        .outerjoin(latest_profile, latest_profile.c.candidate_id == Candidate.id)
        .outerjoin(
            CandidateProfile,
            (CandidateProfile.candidate_id == Candidate.id)
            & (CandidateProfile.version == latest_profile.c.version),
        )
        .order_by(best.c.score.desc())
        .limit(limit)
    ).all()

    return [
        MatchedCandidate(
            candidate_id=row[0],
            name=row[1],
            role=row[2],
            score=float(row[3] or 0),
            years=float(row[4] or 0),
        )
        for row in rows
    ]


@dataclass
class RecentCandidate:
    candidate_id: uuid.UUID
    name: str
    role: str | None
    applied_at: datetime
    #: Latest screening verdict, or the ingestion state when never screened.
    status: str


def recent_candidates(
    session: Session, owner_id: uuid.UUID, limit: int = 5
) -> list[RecentCandidate]:
    """Most recently added candidates, with the closest thing to an application status.

    The design shows Shortlisted / In Review / Submitted / Interview. Those describe
    an applicant-tracking pipeline this system does not have — there is no
    application entity and no stage transitions. What does exist is the screening
    verdict, which carries the same meaning for a recruiter scanning the list, so
    that is what is reported. A candidate nobody has screened shows the ingestion
    state instead of being given a stage they were never in.
    """
    latest_profile = (
        select(
            CandidateProfile.candidate_id.label("candidate_id"),
            func.max(CandidateProfile.version).label("version"),
        )
        .group_by(CandidateProfile.candidate_id)
        .subquery()
    )

    # Best verdict across every screening this candidate appears in. An override
    # wins over the computed value — it is the recruiter's own judgement.
    verdict = (
        select(
            func.coalesce(
                ScreeningResult.override_recommendation, ScreeningResult.recommendation
            )
        )
        .join(Screening, Screening.id == ScreeningResult.screening_id)
        .join(Job, Job.id == Screening.job_id)
        .where(ScreeningResult.candidate_id == Candidate.id, Job.owner_id == owner_id)
        .order_by(ScreeningResult.composite_score.desc())
        .limit(1)
        .correlate(Candidate)
        .scalar_subquery()
    )

    latest_resume = (
        select(Resume.status)
        .where(Resume.candidate_id == Candidate.id)
        .order_by(Resume.version.desc())
        .limit(1)
        .correlate(Candidate)
        .scalar_subquery()
    )

    rows = session.execute(
        select(
            Candidate.id,
            Candidate.full_name,
            CandidateProfile.primary_role,
            Candidate.created_at,
            verdict.label("verdict"),
            latest_resume.label("ingestion"),
        )
        .outerjoin(latest_profile, latest_profile.c.candidate_id == Candidate.id)
        .outerjoin(
            CandidateProfile,
            (CandidateProfile.candidate_id == Candidate.id)
            & (CandidateProfile.version == latest_profile.c.version),
        )
        .where(
            Candidate.owner_id == owner_id,
            Candidate.status == CandidateStatus.ACTIVE.value,
        )
        .order_by(Candidate.created_at.desc())
        .limit(limit)
    ).all()

    return [
        RecentCandidate(
            candidate_id=row[0],
            name=row[1],
            role=row[2],
            applied_at=row[3],
            status=row[4] or row[5] or "unknown",
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Dashboard redesign: attention queue, activity feed, review queue, open jobs.
#
# These back the panels on the recruiter dashboard. Each one is derived from rows
# the recruiter already owns; none of them introduce a counter that can drift.
# ---------------------------------------------------------------------------

#: Stages where the recruiter, not the candidate, owes the next move.
AWAITING_DECISION_STAGES = ("applied", "screening", "shortlisted")


@dataclass
class AttentionItem:
    """One row of the "Needs Attention" panel.

    ``kind`` drives the call to action, so the button label lives with the reason
    rather than being re-derived from the wording in the UI.
    """

    job_id: uuid.UUID
    title: str
    #: review | matches | find | decide
    kind: str
    detail: str
    count: int


@dataclass
class ActivityItem:
    kind: str
    text: str
    at: datetime
    href: str | None = None
    #: Second line — the job or role the event concerns.
    context: str | None = None


@dataclass
class ReviewCandidate:
    candidate_id: uuid.UUID
    name: str
    email: str | None
    role: str | None
    #: Best composite across every screening, or None when never screened.
    match_score: float | None
    years: float
    #: Pipeline stage where one exists, otherwise the ingestion state.
    status: str
    #: Best screening verdict, or None when never screened.
    recommendation: str | None = None


@dataclass
class OpenJobRow:
    id: uuid.UUID
    title: str
    location: str | None
    candidate_count: int
    strong_match_count: int
    days_open: int
    status: str
    #: What the recruiter should do about this job, derived from its counts:
    #: needs_candidates | needs_review | screening, falling back to the stored
    #: status when nothing is outstanding.
    attention_status: str


def _by_job(session: Session, owner_id: uuid.UUID, *conditions) -> dict[uuid.UUID, int]:
    """job_id -> distinct candidates screened against it, under ``conditions``.

    One grouped query for every job rather than one query per row.
    """
    rows = session.execute(
        select(Screening.job_id, func.count(func.distinct(ScreeningResult.candidate_id)))
        .join(ScreeningResult, ScreeningResult.screening_id == Screening.id)
        .join(Job, Job.id == Screening.job_id)
        .where(Job.owner_id == owner_id, *conditions)
        .group_by(Screening.job_id)
    ).all()
    return {row[0]: int(row[1] or 0) for row in rows}


def attention_items(
    session: Session, owner_id: uuid.UUID, limit: int = 4
) -> list[AttentionItem]:
    """Open jobs that are waiting on the recruiter, most urgent first.

    Exactly one reason per job — the most pressing one. A job that has both strong
    matches and candidates awaiting a decision appears once, under the decision,
    because that is the action that actually unblocks someone.
    """
    jobs = session.execute(
        select(Job.id, Job.title)
        .where(Job.owner_id == owner_id, Job.status == JobStatus.OPEN.value)
        .order_by(Job.created_at.desc())
    ).all()
    if not jobs:
        return []

    screened = _by_job(session, owner_id)
    strong = _by_job(session, owner_id, ScreeningResult.composite_score >= STRONG_MATCH_SCORE)

    # Candidates screened against a job with no application opened for that job.
    reviewed = dict(
        session.execute(
            select(Application.job_id, func.count(func.distinct(Application.candidate_id)))
            .join(Job, Job.id == Application.job_id)
            .where(Job.owner_id == owner_id)
            .group_by(Application.job_id)
        ).all()
    )
    awaiting = dict(
        session.execute(
            select(Application.job_id, func.count(Application.id))
            .join(Job, Job.id == Application.job_id)
            .where(
                Job.owner_id == owner_id,
                Application.stage.in_(AWAITING_DECISION_STAGES),
            )
            .group_by(Application.job_id)
        ).all()
    )

    items: list[tuple[int, AttentionItem]] = []
    for job_id, title in jobs:
        seen = screened.get(job_id, 0)
        opened = int(reviewed.get(job_id, 0))
        pending = int(awaiting.get(job_id, 0))
        unreviewed = max(seen - opened, 0)

        # Ordered by how much the recruiter is blocking someone else.
        if pending:
            item = AttentionItem(
                job_id=job_id,
                title=title,
                kind="decide",
                detail=f"{pending} candidate{'s' if pending != 1 else ''} awaiting decision",
                count=pending,
            )
            priority = 0
        elif unreviewed:
            item = AttentionItem(
                job_id=job_id,
                title=title,
                kind="review",
                detail=f"{unreviewed} candidate{'s' if unreviewed != 1 else ''} waiting for review",
                count=unreviewed,
            )
            priority = 1
        elif strong.get(job_id, 0):
            count = strong[job_id]
            item = AttentionItem(
                job_id=job_id,
                title=title,
                kind="matches",
                detail=f"{count} strong match{'es' if count != 1 else ''} found",
                count=count,
            )
            priority = 2
        elif seen == 0:
            item = AttentionItem(
                job_id=job_id,
                title=title,
                kind="find",
                detail="No candidates yet",
                count=0,
            )
            priority = 3
        else:
            # Screened, reviewed, nothing outstanding. Not an attention item.
            continue

        items.append((priority, item))

    items.sort(key=lambda pair: (pair[0], -pair[1].count))
    return [item for _priority, item in items[:limit]]


def activity_feed(session: Session, owner_id: uuid.UUID, limit: int = 5) -> list[ActivityItem]:
    """Recent events across the workspace, newest first.

    Assembled from the tables that already record these things rather than from a
    dedicated activity log: resumes carry their own ingestion timestamps, screenings
    record when they finished, and applications record when they were opened. Each
    source is capped at ``limit`` before merging, so one busy source cannot crowd
    out the others and the merge stays bounded.
    """
    events: list[ActivityItem] = []

    # Resumes that finished parsing.
    latest_role = (
        select(CandidateProfile.primary_role)
        .where(CandidateProfile.candidate_id == Candidate.id)
        .order_by(CandidateProfile.version.desc())
        .limit(1)
        .correlate(Candidate)
        .scalar_subquery()
    )
    for name, at, candidate_id, role in session.execute(
        select(Candidate.full_name, Resume.updated_at, Candidate.id, latest_role)
        .join(Candidate, Candidate.id == Resume.candidate_id)
        .where(Candidate.owner_id == owner_id, Resume.status == "ready")
        .order_by(Resume.updated_at.desc())
        .limit(limit)
    ).all():
        events.append(
            ActivityItem(
                kind="resume",
                text=f"Resume parsed for {name}",
                at=at,
                href=f"/candidates/{candidate_id}",
                context=role,
            )
        )

    # Jobs created.
    for job_id, title, at in session.execute(
        select(Job.id, Job.title, Job.created_at)
        .where(Job.owner_id == owner_id)
        .order_by(Job.created_at.desc())
        .limit(limit)
    ).all():
        events.append(
            ActivityItem(
                kind="job",
                text="New job created",
                at=at,
                href=f"/jobs/{job_id}",
                context=title,
            )
        )

    # Screenings that completed.
    for screening_id, title, at in session.execute(
        select(Screening.id, Job.title, Screening.finished_at)
        .join(Job, Job.id == Screening.job_id)
        .where(
            Job.owner_id == owner_id,
            Screening.status == "completed",
            Screening.finished_at.is_not(None),
        )
        .order_by(Screening.finished_at.desc())
        .limit(limit)
    ).all():
        events.append(
            ActivityItem(
                kind="screening",
                text="Screening completed",
                at=at,
                href=f"/screenings/{screening_id}",
                context=title,
            )
        )

    # Candidates entering the pipeline.
    for name, title, stage, at, candidate_id in session.execute(
        select(
            Candidate.full_name,
            Job.title,
            Application.stage,
            Application.created_at,
            Candidate.id,
        )
        .join(Job, Job.id == Application.job_id)
        .join(Candidate, Candidate.id == Application.candidate_id)
        .where(Job.owner_id == owner_id)
        .order_by(Application.created_at.desc())
        .limit(limit)
    ).all():
        events.append(
            ActivityItem(
                kind="application",
                text=f"{name} {stage.replace('_', ' ')}",
                at=at,
                href=f"/candidates/{candidate_id}",
                context=title,
            )
        )

    # Timestamps come from columns with mixed tz-awareness, so normalise before
    # sorting — comparing an aware datetime with a naive one raises.
    def _key(item: ActivityItem) -> datetime:
        at = item.at
        return at if at.tzinfo else at.replace(tzinfo=UTC)

    events.sort(key=_key, reverse=True)
    return events[:limit]


def review_queue(session: Session, owner_id: uuid.UUID, limit: int = 5) -> list[ReviewCandidate]:
    """Candidates most worth the recruiter's next hour.

    Ordered by best match score descending, nulls last: a candidate nobody has
    screened has no claim on the top of the list, but should not be hidden either,
    so they sort after everyone with a score rather than being filtered out.
    """
    latest_profile = (
        select(
            CandidateProfile.candidate_id.label("candidate_id"),
            func.max(CandidateProfile.version).label("version"),
        )
        .group_by(CandidateProfile.candidate_id)
        .subquery()
    )

    best_score = (
        select(func.max(ScreeningResult.composite_score))
        .join(Screening, Screening.id == ScreeningResult.screening_id)
        .join(Job, Job.id == Screening.job_id)
        .where(ScreeningResult.candidate_id == Candidate.id, Job.owner_id == owner_id)
        .correlate(Candidate)
        .scalar_subquery()
    )

    # The verdict attached to that best score. An override wins over the computed
    # value — it is the recruiter's own judgement.
    verdict = (
        select(
            func.coalesce(
                ScreeningResult.override_recommendation, ScreeningResult.recommendation
            )
        )
        .join(Screening, Screening.id == ScreeningResult.screening_id)
        .join(Job, Job.id == Screening.job_id)
        .where(ScreeningResult.candidate_id == Candidate.id, Job.owner_id == owner_id)
        .order_by(ScreeningResult.composite_score.desc())
        .limit(1)
        .correlate(Candidate)
        .scalar_subquery()
    )

    stage = (
        select(Application.stage)
        .join(Job, Job.id == Application.job_id)
        .where(Application.candidate_id == Candidate.id, Job.owner_id == owner_id)
        .order_by(Application.updated_at.desc())
        .limit(1)
        .correlate(Candidate)
        .scalar_subquery()
    )

    ingestion = (
        select(Resume.status)
        .where(Resume.candidate_id == Candidate.id)
        .order_by(Resume.version.desc())
        .limit(1)
        .correlate(Candidate)
        .scalar_subquery()
    )

    rows = session.execute(
        select(
            Candidate.id,
            Candidate.full_name,
            Candidate.email,
            CandidateProfile.primary_role,
            best_score.label("score"),
            CandidateProfile.total_years_experience,
            stage.label("stage"),
            ingestion.label("ingestion"),
            verdict.label("verdict"),
        )
        .outerjoin(latest_profile, latest_profile.c.candidate_id == Candidate.id)
        .outerjoin(
            CandidateProfile,
            (CandidateProfile.candidate_id == Candidate.id)
            & (CandidateProfile.version == latest_profile.c.version),
        )
        .where(
            Candidate.owner_id == owner_id,
            Candidate.status == CandidateStatus.ACTIVE.value,
        )
        .order_by(best_score.desc().nullslast(), Candidate.created_at.desc())
        .limit(limit)
    ).all()

    return [
        ReviewCandidate(
            candidate_id=row[0],
            name=row[1],
            email=row[2],
            role=row[3],
            match_score=float(row[4]) if row[4] is not None else None,
            years=float(row[5] or 0),
            status=row[6] or row[7] or "unknown",
            recommendation=row[8],
        )
        for row in rows
    ]


def open_jobs(session: Session, owner_id: uuid.UUID, limit: int = 5) -> list[OpenJobRow]:
    """Live roles with the counts the dashboard table shows."""
    jobs = session.execute(
        select(Job.id, Job.title, Job.location, Job.status, Job.created_at)
        .where(Job.owner_id == owner_id, Job.status != JobStatus.CLOSED.value)
        .order_by(Job.created_at.desc())
        .limit(limit)
    ).all()
    if not jobs:
        return []

    screened = _by_job(session, owner_id)
    strong = _by_job(session, owner_id, ScreeningResult.composite_score >= STRONG_MATCH_SCORE)

    # Candidates already opened as applications, so "waiting for review" means
    # screened-but-not-yet-triaged rather than simply screened.
    reviewed = dict(
        session.execute(
            select(Application.job_id, func.count(func.distinct(Application.candidate_id)))
            .join(Job, Job.id == Application.job_id)
            .where(Job.owner_id == owner_id)
            .group_by(Application.job_id)
        ).all()
    )
    # A run still in flight outranks any of the above — the counts are provisional.
    running = {
        row[0]
        for row in session.execute(
            select(Screening.job_id)
            .join(Job, Job.id == Screening.job_id)
            .where(
                Job.owner_id == owner_id,
                Screening.status.notin_(["completed", "failed"]),
            )
            .distinct()
        ).all()
    }
    now = datetime.now(UTC)

    rows = []
    for job_id, title, location, status, created_at in jobs:
        opened = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
        seen = screened.get(job_id, 0)
        unreviewed = max(seen - int(reviewed.get(job_id, 0)), 0)

        if job_id in running:
            attention = "screening"
        elif seen == 0:
            attention = "needs_candidates"
        elif unreviewed:
            attention = "needs_review"
        else:
            # Nothing outstanding: report where the job actually stands.
            attention = status

        rows.append(
            OpenJobRow(
                id=job_id,
                title=title,
                location=location,
                candidate_count=seen,
                strong_match_count=strong.get(job_id, 0),
                days_open=max((now - opened).days, 0),
                status=status,
                attention_status=attention,
            )
        )
    return rows


def pipeline_deltas(
    session: Session, owner_id: uuid.UUID, *, days: int = 7
) -> dict[str, float | None]:
    """Percent change per stage over the last ``days``, against the ``days`` before.

    Measured on entries into a stage, taken from the transition log rather than the
    current occupancy: occupancy only ever tells you where people are standing now,
    which cannot distinguish a stage nobody entered from one everybody passed
    straight through.

    A stage with no entries in the prior window returns None rather than a
    percentage — "up 100%" from zero is noise, and the UI omits the figure instead
    of implying a trend that was never measured.
    """
    now = datetime.now(UTC)
    start = now - timedelta(days=days)
    previous_start = start - timedelta(days=days)

    def _entries(since: datetime, until: datetime) -> dict[str, int]:
        rows = session.execute(
            select(ApplicationEvent.to_stage, func.count(ApplicationEvent.id))
            .join(Application, Application.id == ApplicationEvent.application_id)
            .join(Job, Job.id == Application.job_id)
            .where(
                Job.owner_id == owner_id,
                ApplicationEvent.created_at >= since,
                ApplicationEvent.created_at < until,
            )
            .group_by(ApplicationEvent.to_stage)
        ).all()
        return {row[0]: int(row[1]) for row in rows}

    current = _entries(start, now)
    previous = _entries(previous_start, start)

    return {
        stage.value: _delta(current.get(stage.value, 0), previous.get(stage.value, 0))
        for stage in PIPELINE_ORDER
    }


@dataclass
class SkillCoverage:
    skill: str
    #: Candidates in the latest screening whose resume evidenced this skill.
    matched: int
    #: Candidates that screening evaluated — the denominator.
    of: int


def skill_coverage(
    session: Session, job_id: uuid.UUID, owner_id: uuid.UUID, limit: int = 6
) -> list[SkillCoverage]:
    """How much of the pool actually has each required skill.

    Counted over the most recent completed screening rather than every screening
    ever run: an older run scored a different pool against possibly different
    requirements, and mixing them would make the denominator meaningless.

    Matching is case-insensitive on the normalised skill name, which is how the
    evaluator records `matched_skills` in the first place.
    """
    job = session.execute(
        select(Job).where(Job.id == job_id, Job.owner_id == owner_id)
    ).scalar_one_or_none()
    if job is None or job.requirement is None:
        return []

    required = [
        str(entry.get("skill", "")).strip()
        for entry in (job.requirement.required_skills or [])
        if str(entry.get("skill", "")).strip()
    ]
    if not required:
        return []

    latest = session.execute(
        select(Screening.id)
        .where(Screening.job_id == job_id, Screening.status == "completed")
        .order_by(Screening.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest is None:
        return []

    rows = session.execute(
        select(ScreeningResult.matched_skills).where(ScreeningResult.screening_id == latest)
    ).all()
    if not rows:
        return []

    evaluated = len(rows)
    seen = [{str(skill).strip().lower() for skill in (row[0] or [])} for row in rows]

    coverage = [
        SkillCoverage(
            skill=skill,
            matched=sum(1 for matched in seen if skill.lower() in matched),
            of=evaluated,
        )
        for skill in required
    ]
    coverage.sort(key=lambda entry: (-entry.matched, entry.skill.lower()))
    return coverage[:limit]


@dataclass
class DayActivity:
    day: date
    applied: int
    hired: int


def weekly_activity(
    session: Session, owner_id: uuid.UUID, *, days: int = 7
) -> list[DayActivity]:
    """Applications opened against hires made, per day, zero-filled.

    Zero-filling matters on a chart: a series that skips empty days compresses a
    quiet week into a single step and reads as steady activity.

    Both series come from the transition log rather than current stages, because
    "applied on Tuesday" is an event — the candidate's stage today says nothing
    about when they entered the pipeline.
    """
    start = (datetime.now(UTC) - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    rows = session.execute(
        select(
            func.date(ApplicationEvent.created_at).label("day"),
            ApplicationEvent.to_stage,
            func.count(ApplicationEvent.id),
        )
        .join(Application, Application.id == ApplicationEvent.application_id)
        .join(Job, Job.id == Application.job_id)
        .where(
            Job.owner_id == owner_id,
            ApplicationEvent.created_at >= start,
            ApplicationEvent.to_stage.in_(["applied", "hired"]),
        )
        .group_by(func.date(ApplicationEvent.created_at), ApplicationEvent.to_stage)
    ).all()

    counted: dict[tuple[date, str], int] = {
        (row[0], row[1]): int(row[2]) for row in rows
    }

    first = start.date()
    return [
        DayActivity(
            day=first + timedelta(days=offset),
            applied=counted.get((first + timedelta(days=offset), "applied"), 0),
            hired=counted.get((first + timedelta(days=offset), "hired"), 0),
        )
        for offset in range(days)
    ]

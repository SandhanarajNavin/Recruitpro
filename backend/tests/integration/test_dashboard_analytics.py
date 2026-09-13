"""Dashboard aggregates that are easy to get quietly wrong.

The pipeline deltas are the reason this file exists. They are measured on entries
into a stage rather than on current occupancy, and they must refuse to report a
percentage when the prior window was empty — both are the kind of thing that looks
right on a populated database and lies on a quiet one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.security import hash_password
from app.db.models import (
    Application,
    ApplicationEvent,
    ApplicationStage,
    Candidate,
    User,
)
from app.services import (
    analytics_service,
    application_service,
    interview_service,
    job_service,
)

JOB = (
    "Senior Platform Engineer. Deep Kubernetes and Terraform experience to own our "
    "AWS estate, at least five years in infrastructure, with a track record of "
    "reliability work on production systems."
)


def _database_available() -> bool:
    try:
        engine = create_engine(
            settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 2}
        )
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            connection.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).one()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _database_available(),
    reason="Postgres with pgvector is not reachable — run `docker compose up -d`",
)


@pytest.fixture
def session():
    from app.db.database import session_scope

    db = session_scope()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@pytest.fixture
def owner(session):
    user = User(
        name="Analytics Test",
        email=f"analytics-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password("test-password"),
    )
    session.add(user)
    session.commit()
    yield user
    session.delete(user)
    session.commit()


@pytest.fixture
def job(session, owner):
    return job_service.create_job(
        session, owner_id=owner.id, title="Platform Engineer", description=JOB
    )


def _application(session, owner, job, name: str) -> Application:
    person = Candidate(owner_id=owner.id, full_name=name)
    session.add(person)
    session.commit()
    return application_service.open_application(
        session, owner_id=owner.id, candidate_id=person.id, job_id=job.id
    )


def _backdate(session, application: Application, stage: str, days_ago: float) -> None:
    """Record an entry into ``stage`` at a chosen point in the past.

    Written directly rather than through the service because the service stamps
    ``now`` — and a delta cannot be tested without control of the clock.
    """
    session.add(
        ApplicationEvent(
            application_id=application.id,
            from_stage=None,
            to_stage=stage,
            created_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
    )
    session.commit()


class TestPipelineDeltas:
    def test_empty_prior_window_reports_no_change_rather_than_a_percentage(
        self, session, owner, job
    ):
        application = _application(session, owner, job, "Ada Fresh")
        _backdate(session, application, "screening", days_ago=1)

        deltas = analytics_service.pipeline_deltas(session, owner.id, days=7)

        # One entry now, none before. "Up 100%" from zero would be an invention.
        assert deltas["screening"] is None

    def test_growth_against_a_populated_prior_window(self, session, owner, job):
        application = _application(session, owner, job, "Bo Growth")
        # Prior window: one entry. Current window: three.
        _backdate(session, application, "screening", days_ago=9)
        for offset in (1, 2, 3):
            _backdate(session, application, "screening", days_ago=offset)

        deltas = analytics_service.pipeline_deltas(session, owner.id, days=7)

        assert deltas["screening"] == pytest.approx(200.0)

    def test_decline_is_reported_as_negative(self, session, owner, job):
        application = _application(session, owner, job, "Cy Decline")
        for offset in (8, 9, 10, 11):
            _backdate(session, application, "shortlisted", days_ago=offset)
        _backdate(session, application, "shortlisted", days_ago=2)

        deltas = analytics_service.pipeline_deltas(session, owner.id, days=7)

        assert deltas["shortlisted"] == pytest.approx(-75.0)

    def test_every_stage_is_present_even_when_untouched(self, session, owner, job):
        deltas = analytics_service.pipeline_deltas(session, owner.id, days=7)

        assert set(deltas) == {
            "applied",
            "screening",
            "shortlisted",
            "interview",
            "offer",
            "hired",
        }

    def test_another_recruiters_events_do_not_leak_in(self, session, owner, job):
        """Ownership is a filter, not an afterthought."""
        stranger = User(
            name="Stranger",
            email=f"stranger-{uuid.uuid4().hex[:10]}@example.com",
            password_hash=hash_password("test-password"),
        )
        session.add(stranger)
        session.commit()
        their_job = job_service.create_job(
            session, owner_id=stranger.id, title="Their Role", description=JOB
        )
        theirs = _application(session, stranger, their_job, "Their Candidate")
        _backdate(session, theirs, "screening", days_ago=9)
        for offset in (1, 2, 3):
            _backdate(session, theirs, "screening", days_ago=offset)

        try:
            assert analytics_service.pipeline_deltas(session, owner.id, days=7)[
                "screening"
            ] is None
        finally:
            session.delete(stranger)
            session.commit()

    def test_window_length_changes_what_counts(self, session, owner, job):
        application = _application(session, owner, job, "Di Window")
        _backdate(session, application, "interview", days_ago=20)
        _backdate(session, application, "interview", days_ago=3)

        # 7-day window: the entry 3 days ago is current, the one 20 days ago falls
        # outside both windows, so there is no baseline to compare against.
        assert analytics_service.pipeline_deltas(session, owner.id, days=7)["interview"] is None
        # 15-day window: the 20-day-old entry now lands in the prior window
        # (15-30 days), giving one current against one before — flat.
        assert analytics_service.pipeline_deltas(session, owner.id, days=15)[
            "interview"
        ] == pytest.approx(0.0)


class TestOpenJobs:
    def test_a_job_nobody_has_screened_needs_candidates(self, session, owner, job):
        rows = analytics_service.open_jobs(session, owner.id)
        row = next(r for r in rows if r.id == job.id)

        assert row.attention_status == "needs_candidates"
        assert row.candidate_count == 0

    def test_days_open_is_never_negative(self, session, owner, job):
        rows = analytics_service.open_jobs(session, owner.id)
        assert all(row.days_open >= 0 for row in rows)


class TestJobUpdate:
    def test_status_moves_between_open_hold_and_closed(self, session, owner, job):
        for target in ("on_hold", "closed", "open"):
            updated = job_service.update_job(session, job.id, owner.id, status=target)
            assert updated.status == target

    def test_unknown_status_is_refused(self, session, owner, job):
        with pytest.raises(job_service.JobError, match="Unknown status"):
            job_service.update_job(session, job.id, owner.id, status="archived")

    def test_editable_fields_apply(self, session, owner, job):
        updated = job_service.update_job(
            session,
            job.id,
            owner.id,
            title="Staff Platform Engineer",
            department="Infrastructure",
            employment_type="Full Time",
        )
        assert updated.title == "Staff Platform Engineer"
        assert updated.department == "Infrastructure"
        assert updated.employment_type == "Full Time"

    def test_blank_clears_an_optional_field(self, session, owner, job):
        job_service.update_job(session, job.id, owner.id, department="Infra")
        cleared = job_service.update_job(session, job.id, owner.id, department="   ")
        assert cleared.department is None

    def test_description_cannot_be_edited(self, session, owner, job):
        """It is what the requirements were parsed from."""
        with pytest.raises(job_service.JobError, match="cannot be edited"):
            job_service.update_job(session, job.id, owner.id, description="something else")

    def test_another_recruiter_cannot_edit(self, session, owner, job):
        stranger = User(
            name="Stranger",
            email=f"stranger-{uuid.uuid4().hex[:10]}@example.com",
            password_hash=hash_password("test-password"),
        )
        session.add(stranger)
        session.commit()
        try:
            # Indistinguishable from a job that does not exist.
            with pytest.raises(job_service.JobError, match="not found"):
                job_service.update_job(session, job.id, stranger.id, status="closed")
        finally:
            session.delete(stranger)
            session.commit()


class TestSkillCoverage:
    def test_no_completed_screening_yields_nothing(self, session, owner, job):
        assert analytics_service.skill_coverage(session, job.id, owner.id) == []

    def test_another_recruiters_job_is_invisible(self, session, owner, job):
        stranger = User(
            name="Stranger",
            email=f"stranger-{uuid.uuid4().hex[:10]}@example.com",
            password_hash=hash_password("test-password"),
        )
        session.add(stranger)
        session.commit()
        try:
            assert analytics_service.skill_coverage(session, job.id, stranger.id) == []
        finally:
            session.delete(stranger)
            session.commit()


class TestInterviews:
    @pytest.fixture
    def application(self, session, owner, job):
        return _application(session, owner, job, "Eve Interviewee")

    def test_scheduling_advances_the_stage_so_the_pipeline_reflects_it(
        self, session, owner, application
    ):
        """Booking an interview usually *is* the decision, and a pipeline that
        shows nobody at interview while an interview is booked reads as broken."""
        interview_service.schedule(
            session,
            owner.id,
            application_id=application.id,
            scheduled_at=datetime.now(UTC) + timedelta(days=1),
        )
        session.refresh(application)
        assert application.stage == "interview"

    def test_a_slot_can_be_held_without_advancing(self, session, owner, application):
        before = application.stage
        interview_service.schedule(
            session,
            owner.id,
            application_id=application.id,
            scheduled_at=datetime.now(UTC) + timedelta(days=1),
            advance_stage=False,
        )
        session.refresh(application)
        assert application.stage == before

    def test_an_illegal_stage_move_still_keeps_the_booking(
        self, session, owner, application
    ):
        """The interview is the point of the call; the stage move is a convenience."""
        application_service.move(
            session,
            owner_id=owner.id,
            application_id=application.id,
            to_stage=ApplicationStage.SCREENING,
        )
        application_service.move(
            session,
            owner_id=owner.id,
            application_id=application.id,
            to_stage=ApplicationStage.REJECTED,
        )
        application.stage = "hired"  # nothing is reachable from hired
        session.commit()

        interview = interview_service.schedule(
            session,
            owner.id,
            application_id=application.id,
            scheduled_at=datetime.now(UTC) + timedelta(days=1),
        )
        session.refresh(application)
        assert interview.id is not None
        assert application.stage == "hired"

    def test_windows_partition_by_time(self, session, owner, application):
        past = interview_service.schedule(
            session,
            owner.id,
            application_id=application.id,
            scheduled_at=datetime.now(UTC) - timedelta(days=3),
        )
        future = interview_service.schedule(
            session,
            owner.id,
            application_id=application.id,
            scheduled_at=datetime.now(UTC) + timedelta(days=3),
        )

        upcoming = [row.id for row in interview_service.list_interviews(
            session, owner.id, window="upcoming"
        )]
        earlier = [row.id for row in interview_service.list_interviews(
            session, owner.id, window="past"
        )]

        assert future.id in upcoming and past.id not in upcoming
        assert past.id in earlier and future.id not in earlier

    def test_upcoming_is_soonest_first(self, session, owner, application):
        far = interview_service.schedule(
            session, owner.id, application_id=application.id,
            scheduled_at=datetime.now(UTC) + timedelta(days=9),
        )
        soon = interview_service.schedule(
            session, owner.id, application_id=application.id,
            scheduled_at=datetime.now(UTC) + timedelta(days=1),
        )
        order = [row.id for row in interview_service.list_interviews(
            session, owner.id, window="upcoming"
        )]
        assert order.index(soon.id) < order.index(far.id)

    def test_cancelling_keeps_the_record(self, session, owner, application):
        interview = interview_service.schedule(
            session, owner.id, application_id=application.id,
            scheduled_at=datetime.now(UTC) + timedelta(days=1),
        )
        cancelled = interview_service.cancel(session, owner.id, interview.id)

        assert cancelled.outcome == "cancelled"
        # Still listed: a slot booked and dropped is part of the history.
        assert interview.id in [
            row.id for row in interview_service.list_interviews(
                session, owner.id, window="all"
            )
        ]

    def test_unknown_outcome_is_refused(self, session, owner, application):
        interview = interview_service.schedule(
            session, owner.id, application_id=application.id,
            scheduled_at=datetime.now(UTC) + timedelta(days=1),
        )
        with pytest.raises(interview_service.InterviewError, match="Unknown outcome"):
            interview_service.record(session, owner.id, interview.id, outcome="maybe")

    def test_unknown_window_is_refused(self, session, owner):
        with pytest.raises(interview_service.InterviewError, match="Unknown window"):
            interview_service.list_interviews(session, owner.id, window="yesterday")

    def test_another_recruiter_cannot_see_or_touch_it(self, session, owner, application):
        interview = interview_service.schedule(
            session, owner.id, application_id=application.id,
            scheduled_at=datetime.now(UTC) + timedelta(days=1),
        )
        stranger = User(
            name="Stranger",
            email=f"stranger-{uuid.uuid4().hex[:10]}@example.com",
            password_hash=hash_password("test-password"),
        )
        session.add(stranger)
        session.commit()
        try:
            assert interview_service.list_interviews(session, stranger.id, window="all") == []
            with pytest.raises(interview_service.InterviewError, match="not found"):
                interview_service.record(
                    session, stranger.id, interview.id, outcome="completed"
                )
        finally:
            session.delete(stranger)
            session.commit()

    def test_cannot_book_against_a_rejected_application(self, session, owner, application):
        application.stage = "rejected"
        session.commit()
        with pytest.raises(interview_service.InterviewError, match="rejected"):
            interview_service.schedule(
                session, owner.id, application_id=application.id,
                scheduled_at=datetime.now(UTC) + timedelta(days=1),
            )

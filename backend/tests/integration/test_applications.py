"""The hiring pipeline: stage transitions, history and isolation.

The stage machine is the thing that makes funnel reporting mean anything. If a
candidate can appear at "offer" without passing through "interview", the pipeline
numbers describe nothing.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, select, text

from app.core.config import settings
from app.core.security import hash_password
from app.db.models import (
    Application,
    ApplicationEvent,
    ApplicationStage,
    Candidate,
    User,
)
from app.services import application_service, job_service
from app.services.application_service import ApplicationError

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
        name="Pipeline Test",
        email=f"pipe-{uuid.uuid4().hex[:10]}@example.com",
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


@pytest.fixture
def candidate(session, owner):
    person = Candidate(owner_id=owner.id, full_name="Dana Okonkwo")
    session.add(person)
    session.commit()
    return person


@pytest.fixture
def application(session, owner, job, candidate):
    return application_service.open_application(
        session, owner_id=owner.id, candidate_id=candidate.id, job_id=job.id
    )


class TestOpening:
    def test_starts_at_applied_with_an_event(self, session, application):
        assert application.stage == ApplicationStage.APPLIED.value
        events = session.execute(
            select(ApplicationEvent).where(
                ApplicationEvent.application_id == application.id
            )
        ).scalars().all()
        assert len(events) == 1
        assert events[0].from_stage is None
        assert events[0].to_stage == ApplicationStage.APPLIED.value

    def test_opening_twice_returns_the_same_application(
        self, session, owner, job, candidate, application
    ):
        """Shortlisting is a button a recruiter will press twice."""
        again = application_service.open_application(
            session, owner_id=owner.id, candidate_id=candidate.id, job_id=job.id
        )
        assert again.id == application.id
        total = session.execute(
            select(Application).where(Application.candidate_id == candidate.id)
        ).scalars().all()
        assert len(total) == 1

    def test_cannot_open_against_another_recruiters_job(self, session, owner, candidate):
        intruder = User(
            name="Intruder",
            email=f"x-{uuid.uuid4().hex[:10]}@example.com",
            password_hash=hash_password("test-password"),
        )
        session.add(intruder)
        session.commit()
        try:
            other_job = job_service.create_job(
                session, owner_id=intruder.id, title="Theirs", description=JOB
            )
            with pytest.raises(ApplicationError):
                application_service.open_application(
                    session,
                    owner_id=owner.id,
                    candidate_id=candidate.id,
                    job_id=other_job.id,
                )
        finally:
            session.delete(intruder)
            session.commit()


class TestTransitions:
    def test_full_happy_path(self, session, owner, application):
        for stage in (
            ApplicationStage.SCREENING,
            ApplicationStage.SHORTLISTED,
            ApplicationStage.INTERVIEW,
            ApplicationStage.OFFER,
            ApplicationStage.HIRED,
        ):
            application_service.move(
                session, owner_id=owner.id, application_id=application.id, to_stage=stage
            )
        session.refresh(application)
        assert application.stage == ApplicationStage.HIRED.value
        assert application.closed_at is not None

    def test_cannot_skip_stages(self, session, owner, application):
        """applied -> offer would put someone in the funnel who never passed through
        the stages above, which makes every count above them a lie."""
        with pytest.raises(ApplicationError, match="Cannot move from applied to offer"):
            application_service.move(
                session,
                owner_id=owner.id,
                application_id=application.id,
                to_stage=ApplicationStage.OFFER,
            )

    def test_cannot_move_backwards(self, session, owner, application):
        application_service.move(
            session,
            owner_id=owner.id,
            application_id=application.id,
            to_stage=ApplicationStage.SCREENING,
        )
        with pytest.raises(ApplicationError):
            application_service.move(
                session,
                owner_id=owner.id,
                application_id=application.id,
                to_stage=ApplicationStage.APPLIED,
            )

    def test_rejection_is_reachable_from_any_active_stage(self, session, owner, application):
        application_service.move(
            session,
            owner_id=owner.id,
            application_id=application.id,
            to_stage=ApplicationStage.SCREENING,
        )
        rejected = application_service.move(
            session,
            owner_id=owner.id,
            application_id=application.id,
            to_stage=ApplicationStage.REJECTED,
            note="Not enough infrastructure depth.",
        )
        assert rejected.stage == ApplicationStage.REJECTED.value
        assert rejected.rejection_reason == "Not enough infrastructure depth."
        assert rejected.closed_at is not None

    def test_a_rejection_can_be_reopened(self, session, owner, application):
        """Recruiters change their minds; the alternative is a duplicate application
        that violates the unique constraint."""
        application_service.move(
            session,
            owner_id=owner.id,
            application_id=application.id,
            to_stage=ApplicationStage.REJECTED,
        )
        reopened = application_service.move(
            session,
            owner_id=owner.id,
            application_id=application.id,
            to_stage=ApplicationStage.SHORTLISTED,
        )
        assert reopened.stage == ApplicationStage.SHORTLISTED.value
        assert reopened.closed_at is None, "reopening must clear the close timestamp"

    def test_hired_is_terminal(self, session, owner, application):
        for stage in (
            ApplicationStage.SCREENING,
            ApplicationStage.SHORTLISTED,
            ApplicationStage.INTERVIEW,
            ApplicationStage.OFFER,
            ApplicationStage.HIRED,
        ):
            application_service.move(
                session, owner_id=owner.id, application_id=application.id, to_stage=stage
            )
        with pytest.raises(ApplicationError):
            application_service.move(
                session,
                owner_id=owner.id,
                application_id=application.id,
                to_stage=ApplicationStage.REJECTED,
            )

    def test_moving_to_the_current_stage_is_a_no_op(self, session, owner, application):
        before = session.execute(
            select(ApplicationEvent).where(
                ApplicationEvent.application_id == application.id
            )
        ).scalars().all()
        application_service.move(
            session,
            owner_id=owner.id,
            application_id=application.id,
            to_stage=ApplicationStage.APPLIED,
        )
        after = session.execute(
            select(ApplicationEvent).where(
                ApplicationEvent.application_id == application.id
            )
        ).scalars().all()
        assert len(after) == len(before), "a no-op must not write a history row"

    def test_every_move_is_recorded(self, session, owner, application):
        application_service.move(
            session,
            owner_id=owner.id,
            application_id=application.id,
            to_stage=ApplicationStage.SCREENING,
            actor_id=owner.id,
        )
        events = session.execute(
            select(ApplicationEvent)
            .where(ApplicationEvent.application_id == application.id)
            .order_by(ApplicationEvent.created_at)
        ).scalars().all()
        assert [e.to_stage for e in events] == ["applied", "screening"]
        assert events[-1].from_stage == "applied"
        assert events[-1].actor_id == owner.id


class TestPipelineCounts:
    def test_every_stage_appears_even_at_zero(self, session, owner, application):
        counts = application_service.pipeline_counts(session, owner.id)
        assert set(counts) == {
            "applied",
            "screening",
            "shortlisted",
            "interview",
            "offer",
            "hired",
        }
        assert counts["applied"] == 1
        assert counts["hired"] == 0

    def test_counts_follow_the_move(self, session, owner, application):
        application_service.move(
            session,
            owner_id=owner.id,
            application_id=application.id,
            to_stage=ApplicationStage.SCREENING,
        )
        counts = application_service.pipeline_counts(session, owner.id)
        assert counts["applied"] == 0
        assert counts["screening"] == 1

    def test_another_recruiter_sees_nothing(self, session, application):
        stranger = User(
            name="Stranger",
            email=f"s-{uuid.uuid4().hex[:10]}@example.com",
            password_hash=hash_password("test-password"),
        )
        session.add(stranger)
        session.commit()
        try:
            counts = application_service.pipeline_counts(session, stranger.id)
            assert sum(counts.values()) == 0
        finally:
            session.delete(stranger)
            session.commit()

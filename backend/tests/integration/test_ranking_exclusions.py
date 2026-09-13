"""Who is eligible to appear in a job's rankings.

Two separate rules, easy to conflate:

* A hire is global. Someone already placed stops competing for every role — leaving
  them in would have them displacing real candidates on other roles.
* A removal is per job. "Not for this role" says nothing about any other role, so
  the same candidate must still appear everywhere else.

These go through the repository rather than the endpoint because the rules are SQL
gates, and a gate that is wrong is wrong for every caller.
"""

from __future__ import annotations

import uuid

import pytest
from seed_data import SAMPLE_CANDIDATES, SAMPLE_JOB_DESCRIPTION
from sqlalchemy import create_engine, select, text

from app.core.config import settings
from app.core.security import hash_password
from app.db.models import (
    Application,
    ApplicationStage,
    Candidate,
    JobCandidateExclusion,
    Resume,
    User,
)
from app.db.repositories import candidate_repository as repo
from app.services import exclusion_service, job_service, resume_service


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
        name="Exclusion Test",
        email=f"excl-{uuid.uuid4().hex[:12]}@example.com",
        password_hash=hash_password("test-password"),
    )
    session.add(user)
    session.commit()
    yield user
    session.delete(user)
    session.commit()


def _ingest(session, owner, limit: int) -> list[Resume]:
    resumes = []
    for _external_id, name, resume_text in SAMPLE_CANDIDATES[:limit]:
        resume = resume_service.accept_upload(
            session,
            owner_id=owner.id,
            filename=f"{name.replace(' ', '_')}.txt",
            content_type="text/plain",
            data=resume_text.encode("utf-8"),
        )
        resume_service.process_resume(session, resume.id)
        resumes.append(resume)
    return resumes


@pytest.fixture
def repository(session, owner):
    """Three candidates and two jobs, so "this job only" is actually testable."""
    _ingest(session, owner, 3)
    first = job_service.create_job(
        session, owner_id=owner.id, title="Role A", description=SAMPLE_JOB_DESCRIPTION
    )
    second = job_service.create_job(
        session, owner_id=owner.id, title="Role B", description=SAMPLE_JOB_DESCRIPTION
    )
    candidates = (
        session.execute(select(Candidate).where(Candidate.owner_id == owner.id)).scalars().all()
    )
    return owner, first, second, candidates


def _pool(session, owner_id, job_id):
    return repo.count_pool(session, owner_id, job_id=job_id)


class TestHiredCandidatesLeaveEveryRanking:
    def test_a_hire_removes_them_from_the_job_they_were_hired_for(self, session, repository):
        owner, job, _other, candidates = repository
        before = _pool(session, owner.id, job.id)

        session.add(
            Application(
                candidate_id=candidates[0].id,
                job_id=job.id,
                stage=ApplicationStage.HIRED.value,
            )
        )
        session.commit()

        assert _pool(session, owner.id, job.id) == before - 1

    def test_and_from_every_other_job_too(self, session, repository):
        """The whole point: a placed candidate stops competing everywhere."""
        owner, job, other, candidates = repository
        before = _pool(session, owner.id, other.id)

        session.add(
            Application(
                candidate_id=candidates[0].id,
                job_id=job.id,
                stage=ApplicationStage.HIRED.value,
            )
        )
        session.commit()

        assert _pool(session, owner.id, other.id) == before - 1

    @pytest.mark.parametrize(
        "stage",
        [ApplicationStage.SHORTLISTED, ApplicationStage.INTERVIEW, ApplicationStage.OFFER],
    )
    def test_earlier_stages_leave_them_in_play(self, session, repository, stage):
        # Only a hire is terminal. An offer can still be declined, so someone at
        # offer must keep competing.
        owner, job, other, candidates = repository
        before = _pool(session, owner.id, other.id)

        session.add(Application(candidate_id=candidates[0].id, job_id=job.id, stage=stage.value))
        session.commit()

        assert _pool(session, owner.id, other.id) == before

    def test_a_rejection_leaves_them_in_the_pool(self, session, repository):
        # Rejected is per job and deliberately not a pool gate: the recruiter wants
        # to see the name and decide, which is what the marker on the row is for.
        owner, job, _other, candidates = repository
        before = _pool(session, owner.id, job.id)

        session.add(
            Application(
                candidate_id=candidates[0].id,
                job_id=job.id,
                stage=ApplicationStage.REJECTED.value,
            )
        )
        session.commit()

        assert _pool(session, owner.id, job.id) == before


class TestRemovalIsScopedToOneJob:
    def test_removed_from_this_job(self, session, repository):
        owner, job, _other, candidates = repository
        before = _pool(session, owner.id, job.id)

        exclusion_service.exclude(session, job_id=job.id, candidate_id=candidates[0].id)

        assert _pool(session, owner.id, job.id) == before - 1

    def test_but_still_competing_for_every_other_job(self, session, repository):
        owner, job, other, candidates = repository
        before = _pool(session, owner.id, other.id)

        exclusion_service.exclude(session, job_id=job.id, candidate_id=candidates[0].id)

        assert _pool(session, owner.id, other.id) == before

    def test_removing_twice_is_one_removal(self, session, repository):
        _owner, job, _other, candidates = repository
        first = exclusion_service.exclude(session, job_id=job.id, candidate_id=candidates[0].id)
        again = exclusion_service.exclude(session, job_id=job.id, candidate_id=candidates[0].id)

        assert first.id == again.id
        rows = (
            session.execute(
                select(JobCandidateExclusion).where(JobCandidateExclusion.job_id == job.id)
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1

    def test_restore_puts_them_back(self, session, repository):
        owner, job, _other, candidates = repository
        before = _pool(session, owner.id, job.id)

        exclusion_service.exclude(session, job_id=job.id, candidate_id=candidates[0].id)
        assert exclusion_service.restore(session, job_id=job.id, candidate_id=candidates[0].id)

        assert _pool(session, owner.id, job.id) == before

    def test_restoring_something_not_removed_reports_nothing_done(self, session, repository):
        _owner, job, _other, candidates = repository
        assert not exclusion_service.restore(
            session, job_id=job.id, candidate_id=candidates[0].id
        )

    def test_the_filtered_count_applies_the_same_gates(self, session, repository):
        """A pool the funnel reports but never searched would be a lie."""
        owner, job, _other, candidates = repository
        before = repo.count_after_filters(
            session, owner.id, must_have_skills=[], min_years=0, job_id=job.id
        )

        exclusion_service.exclude(session, job_id=job.id, candidate_id=candidates[0].id)

        after = repo.count_after_filters(
            session, owner.id, must_have_skills=[], min_years=0, job_id=job.id
        )
        assert after == before - 1

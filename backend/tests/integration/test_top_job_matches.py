"""Best-fit jobs for one candidate.

The inverse of a screening: a screening asks "who fits this job", this asks "which
job does this person fit". It answers from scores that were already produced, because
a fit for a job never screened would be a number with no run behind it.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.security import hash_password
from app.db.models import (
    Candidate,
    Job,
    Screening,
    ScreeningResult,
    User,
)
from app.db.models.screening import ScreeningStatus
from app.services import candidate_service


def _database_available() -> bool:
    try:
        engine = create_engine(
            settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 2}
        )
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _database_available(),
    reason="Postgres is not reachable — run `docker compose up -d`",
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
        name="Match Test",
        email=f"match-{uuid.uuid4().hex[:12]}@example.com",
        password_hash=hash_password("test-password"),
    )
    session.add(user)
    session.commit()
    yield user
    session.delete(user)
    session.commit()


@pytest.fixture
def candidate(session, owner):
    row = Candidate(owner_id=owner.id, full_name="Test Person")
    session.add(row)
    session.commit()
    return row


def _job(session, owner, title: str) -> Job:
    # No JobRequirement: this ranking reads stored scores and never touches the
    # parsed requirements, so adding one would only obscure what it depends on.
    job = Job(owner_id=owner.id, title=title, description="x")
    session.add(job)
    session.commit()
    return job


def _score(session, job, candidate, score: float, *, recommendation="maybe", shortlisted=True):
    screening = Screening(job_id=job.id, status=ScreeningStatus.COMPLETED.value)
    session.add(screening)
    session.flush()
    session.add(
        ScreeningResult(
            screening_id=screening.id,
            candidate_id=candidate.id,
            rank=1,
            composite_score=score,
            recommendation=recommendation,
            shortlisted=shortlisted,
        )
    )
    session.commit()
    return screening


class TestTopJobMatches:
    def test_nothing_scored_yet_is_empty_rather_than_zero(self, session, owner, candidate):
        # An unscored candidate is not a candidate who scored nothing.
        matches, unscored = candidate_service.top_job_matches(session, candidate.id, owner.id)
        assert matches == []
        assert unscored == 0

    def test_jobs_come_back_best_first(self, session, owner, candidate):
        low = _job(session, owner, "Low fit")
        high = _job(session, owner, "High fit")
        middle = _job(session, owner, "Middle fit")
        _score(session, low, candidate, 41.0)
        _score(session, high, candidate, 92.5)
        _score(session, middle, candidate, 66.0)

        matches, unscored = candidate_service.top_job_matches(session, candidate.id, owner.id)

        assert [row["job_title"] for row in matches] == ["High fit", "Middle fit", "Low fit"]
        assert matches[0]["score"] == pytest.approx(92.5)
        assert unscored == 0

    def test_a_job_screened_twice_appears_once_at_its_best_score(
        self, session, owner, candidate
    ):
        """A re-run with a wider pool does not make the earlier score untrue, and the
        job must not be listed twice."""
        job = _job(session, owner, "Screened twice")
        _score(session, job, candidate, 55.0)
        _score(session, job, candidate, 78.0)

        matches, _unscored = candidate_service.top_job_matches(session, candidate.id, owner.id)

        assert len(matches) == 1
        assert matches[0]["score"] == pytest.approx(78.0)

    def test_the_limit_caps_the_list_without_hiding_the_remainder(
        self, session, owner, candidate
    ):
        for index in range(7):
            _score(session, _job(session, owner, f"Job {index}"), candidate, 50.0 + index)

        matches, unscored = candidate_service.top_job_matches(
            session, candidate.id, owner.id, limit=5
        )

        assert len(matches) == 5
        # Highest first, so the two dropped are the weakest fits.
        assert [row["job_title"] for row in matches] == [f"Job {i}" for i in (6, 5, 4, 3, 2)]
        # Every job was scored, so nothing is outstanding even though two are unlisted.
        assert unscored == 0

    def test_unscored_jobs_are_counted_not_ranked(self, session, owner, candidate):
        scored = _job(session, owner, "Scored")
        _job(session, owner, "Never screened")
        _job(session, owner, "Also never screened")
        _score(session, scored, candidate, 70.0)

        matches, unscored = candidate_service.top_job_matches(session, candidate.id, owner.id)

        # Ranking only what actually ran, and saying how much it does not cover.
        assert [row["job_title"] for row in matches] == ["Scored"]
        assert unscored == 2

    def test_the_breakdown_comes_through_for_the_expanded_row(self, session, owner, candidate):
        """The row expands in place, so it carries the same breakdown the job page
        shows for the same pair rather than a summary of it."""
        job = _job(session, owner, "With evidence")
        screening = _score(session, job, candidate, 70.0)
        result = (
            session.query(ScreeningResult)
            .filter(ScreeningResult.screening_id == screening.id)
            .one()
        )
        result.subscores = {
            "required_skills": {"score": 80.0, "weight": 0.35, "contribution": 28.0},
            "experience": {"score": 60.0, "weight": 0.2, "contribution": 12.0},
        }
        result.matched_skills = ["Python", "AWS"]
        result.missing_skills = ["Kubernetes"]
        result.evidence = {"strengths": ["Deep AWS work"], "concerns": ["No k8s"]}
        session.commit()

        matches, _unscored = candidate_service.top_job_matches(session, candidate.id, owner.id)

        row = matches[0]
        assert [entry["label"] for entry in row["subscores"]] == ["Required skills", "Experience"]
        assert row["subscores"][0]["contribution"] == pytest.approx(28.0)
        assert row["matched_skills"] == ["Python", "AWS"]
        assert row["missing_skills"] == ["Kubernetes"]
        assert row["evidence"]["strengths"] == ["Deep AWS work"]

    def test_a_category_the_run_never_recorded_is_omitted_not_zeroed(
        self, session, owner, candidate
    ):
        # A missing category is unknown, not a score of nothing — the same reason
        # the candidate list shows a dash rather than 0% for an unscreened person.
        job = _job(session, owner, "Partial breakdown")
        screening = _score(session, job, candidate, 70.0)
        result = (
            session.query(ScreeningResult)
            .filter(ScreeningResult.screening_id == screening.id)
            .one()
        )
        result.subscores = {"role": {"score": 90.0, "weight": 0.15, "contribution": 13.5}}
        session.commit()

        matches, _unscored = candidate_service.top_job_matches(session, candidate.id, owner.id)

        assert [entry["category"] for entry in matches[0]["subscores"]] == ["role"]

    def test_an_override_wins_over_the_computed_recommendation(
        self, session, owner, candidate
    ):
        job = _job(session, owner, "Overridden")
        screening = _score(session, job, candidate, 70.0, recommendation="pass")
        result = (
            session.query(ScreeningResult)
            .filter(ScreeningResult.screening_id == screening.id)
            .one()
        )
        result.override_recommendation = "interview"
        session.commit()

        matches, _unscored = candidate_service.top_job_matches(session, candidate.id, owner.id)

        assert matches[0]["recommendation"] == "interview"

    def test_another_recruiters_jobs_are_not_visible(self, session, owner, candidate):
        other = User(
            name="Someone Else",
            email=f"other-{uuid.uuid4().hex[:12]}@example.com",
            password_hash=hash_password("test-password"),
        )
        session.add(other)
        session.commit()
        try:
            theirs = _job(session, other, "Their job")
            _score(session, theirs, candidate, 99.0)

            matches, unscored = candidate_service.top_job_matches(
                session, candidate.id, owner.id
            )

            assert matches == []
            assert unscored == 0
        finally:
            session.delete(other)
            session.commit()

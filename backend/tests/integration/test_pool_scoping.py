"""The candidate pool, and the tenant scoping of the query that builds it.

``_LATEST_PROFILE_CTE`` was rewritten to filter by owner inside the CTE rather than
letting the outer query join an all-tenant result down to size. That is a pure
performance change and must not alter who is in the pool, so these pin the pool
itself: the right candidates, the newest profile version, and nothing belonging to
anyone else.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.security import hash_password
from app.db.models import Candidate, CandidateProfile, Resume, ResumeStatus, User
from app.db.repositories import candidate_repository as repo


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


def _recruiter(session, label: str) -> User:
    user = User(
        name=f"Pool {label}",
        email=f"pool-{label}-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password("test-password"),
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture
def alice(session):
    user = _recruiter(session, "alice")
    yield user
    session.delete(user)
    session.commit()


@pytest.fixture
def bob(session):
    user = _recruiter(session, "bob")
    yield user
    session.delete(user)
    session.commit()


def _candidate(session, owner, name, *, years=5.0, skills=("Python",), versions=1, status="active"):
    """A candidate whose newest profile carries `years` and `skills`.

    Older versions deliberately carry different values, so a query that reads the
    wrong version fails visibly rather than coincidentally agreeing.
    """
    candidate = Candidate(owner_id=owner.id, full_name=name, status=status)
    session.add(candidate)
    session.flush()
    resume = Resume(
        candidate_id=candidate.id,
        storage_key=f"k/{uuid.uuid4().hex}",
        original_filename="r.txt",
        content_type="text/plain",
        size_bytes=1,
        status=ResumeStatus.READY.value,
    )
    session.add(resume)
    session.flush()
    for version in range(1, versions + 1):
        newest = version == versions
        session.add(
            CandidateProfile(
                candidate_id=candidate.id,
                resume_id=resume.id,
                total_years_experience=years if newest else 0,
                skills=list(skills) if newest else ["Fortran"],
                parser_model="test",
                version=version,
            )
        )
    session.commit()
    return candidate


class TestPoolMembership:
    def test_counts_this_recruiters_candidates(self, session, alice):
        _candidate(session, alice, "One")
        _candidate(session, alice, "Two")

        assert repo.count_pool(session, alice.id) == 2

    def test_another_recruiters_candidates_are_invisible(self, session, alice, bob):
        _candidate(session, alice, "Mine")
        _candidate(session, bob, "Theirs")
        _candidate(session, bob, "Also theirs")

        # The regression the CTE rewrite could have introduced: scoping the CTE by
        # owner must not change the answer, only how fast it arrives.
        assert repo.count_pool(session, alice.id) == 1
        assert repo.count_pool(session, bob.id) == 2

    def test_a_candidate_with_no_profile_is_not_in_the_pool(self, session, alice):
        _candidate(session, alice, "Parsed")
        # Uploaded but never parsed: nothing to screen against.
        session.add(Candidate(owner_id=alice.id, full_name="Unparsed"))
        session.commit()

        assert repo.count_pool(session, alice.id) == 1

    def test_archived_candidates_are_out(self, session, alice):
        _candidate(session, alice, "Active")
        _candidate(session, alice, "Archived", status="archived")

        assert repo.count_pool(session, alice.id) == 1


class TestOnlyTheNewestProfileCounts:
    def test_filters_read_the_newest_version(self, session, alice):
        # Four versions; only the newest says 9 years and knows Kubernetes.
        _candidate(session, alice, "Versioned", years=9.0, skills=("Kubernetes",), versions=4)

        assert (
            repo.count_after_filters(
                session, alice.id, must_have_skills=["Kubernetes"], min_years=8
            )
            == 1
        )
        # The superseded versions must not qualify anyone.
        assert (
            repo.count_after_filters(
                session, alice.id, must_have_skills=["Fortran"], min_years=0
            )
            == 0
        )

    def test_the_gate_is_case_insensitive(self, session, alice):
        _candidate(session, alice, "Postgres person", skills=("PostgreSQL",))

        assert (
            repo.count_after_filters(
                session, alice.id, must_have_skills=["postgresql"], min_years=0
            )
            == 1
        )

    def test_every_must_have_skill_is_required(self, session, alice):
        _candidate(session, alice, "Partial", skills=("Python",))

        assert (
            repo.count_after_filters(
                session, alice.id, must_have_skills=["Python", "Go"], min_years=0
            )
            == 0
        )

    def test_years_gate_reads_the_newest_version(self, session, alice):
        _candidate(session, alice, "Junior now", years=1.0, versions=3)

        assert repo.count_after_filters(
            session, alice.id, must_have_skills=[], min_years=5
        ) == 0


class TestScopingIsInTheQueryNotTheCaller:
    def test_the_cte_binds_owner(self):
        """The CTE now needs :owner, so a new statement that forgets to bind it
        fails loudly instead of quietly scanning every tenant again."""
        assert ":owner" in repo._LATEST_PROFILE_CTE

    def test_a_statement_without_the_owner_parameter_errors(self, session, alice):
        from sqlalchemy.exc import StatementError

        sql = repo._LATEST_PROFILE_CTE + " SELECT count(*) FROM latest"
        with pytest.raises(StatementError):
            session.execute(text(sql))

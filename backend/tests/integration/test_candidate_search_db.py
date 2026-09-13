"""Free-text candidate search against Postgres.

The SQL half of the search cannot be unit tested: the bug that made a role search
match nothing was an aggregate subquery that silently correlated to the wrong table,
which only shows up once the statement actually runs.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.security import hash_password
from app.db.models import Candidate, CandidateProfile, Resume, ResumeStatus, User
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
def recruiter(session):
    user = User(
        name="Search Test",
        email=f"search-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password("test-password"),
    )
    session.add(user)
    session.commit()
    yield user
    session.delete(user)
    session.commit()


def _candidate(session, owner_id, name, title, primary_role, secondary=(), versions=1):
    """A candidate with `versions` profile versions, the newest carrying the role."""
    candidate = Candidate(owner_id=owner_id, full_name=name)
    session.add(candidate)
    session.flush()
    resume = Resume(
        candidate_id=candidate.id,
        storage_key=f"test/{uuid.uuid4().hex}.txt",
        original_filename="x.txt",
        content_type="text/plain",
        size_bytes=1,
        checksum=uuid.uuid4().hex,
        status=ResumeStatus.READY.value,
        extracted_text="x",
    )
    session.add(resume)
    session.flush()
    for version in range(1, versions + 1):
        newest = version == versions
        session.add(
            CandidateProfile(
                candidate_id=candidate.id,
                resume_id=resume.id,
                # Only the newest version carries the real role, so a search that
                # reads the wrong version is visible as a miss.
                current_title=title if newest else "Intern",
                primary_role=primary_role if newest else "Intern",
                secondary_roles=list(secondary) if newest else [],
                total_years_experience=6,
                parser_model="test",
                version=version,
            )
        )
    session.commit()
    return candidate


@pytest.fixture
def repository(session, recruiter):
    """One of each shape the search has to cope with."""
    _candidate(session, recruiter.id, "Grace Hyphen", "Full-Stack Developer", "Full Stack Engineer")
    _candidate(session, recruiter.id, "Dana Ops", "DevOps Engineer", "DevOps Engineer")
    _candidate(session, recruiter.id, "Rafael Learn", "Machine Learning Engineer", "AI Engineer")
    # Several versions: the role search must read only the latest.
    _candidate(
        session, recruiter.id, "Vera Versioned", "Backend Engineer", "Backend Engineer",
        versions=4,
    )
    # No profile at all — must still be findable by name, and must not error.
    unparsed = Candidate(owner_id=recruiter.id, full_name="Unparsed Person")
    session.add(unparsed)
    session.commit()
    return recruiter


def _names(session, owner_id, query, fuzzy=True):
    rows, total = candidate_service.list_candidates(
        session, owner_id, query=query, fuzzy=fuzzy
    )
    assert total == len(rows)
    return sorted(row.full_name for row in rows)


def _exact(session, owner_id, query):
    """Search with the fuzzy retry off.

    The retry would otherwise mask a broken exact match by finding the same rows a
    second way — which is exactly what happened to the first version of these tests.
    """
    return _names(session, owner_id, query, fuzzy=False)


class TestExactSearch:
    def test_a_role_is_found_by_its_title(self, session, repository):
        assert _exact(session, repository.id, "full-stack developer") == ["Grace Hyphen"]

    def test_spacing_and_punctuation_are_ignored(self, session, repository):
        # All three spellings are the same role.
        for query in ("fullstack developer", "Full Stack Developer", "full-stack developer"):
            assert _exact(session, repository.id, query) == ["Grace Hyphen"]

    def test_a_candidate_is_found_by_primary_role(self, session, repository):
        assert _exact(session, repository.id, "ai engineer") == ["Rafael Learn"]

    def test_name_search_still_works_without_a_profile(self, session, repository):
        assert _exact(session, repository.id, "Unparsed") == ["Unparsed Person"]

    def test_only_the_latest_profile_version_is_searched(self, session, repository):
        # Vera's older versions say "Intern"; her latest says Backend Engineer.
        assert _exact(session, repository.id, "backend engineer") == ["Vera Versioned"]
        assert _exact(session, repository.id, "intern") == []
        # Grace has only a version 1: a search must not require the newest version
        # number in the table, only the newest for that candidate.
        assert _exact(session, repository.id, "full stack engineer") == ["Grace Hyphen"]

    def test_a_punctuation_only_query_does_not_error(self, session, repository):
        assert _exact(session, repository.id, "--") == []


class TestFuzzyFallback:
    @pytest.mark.parametrize("query", ["fulstak", "fullstak", "fulstak developer"])
    def test_a_misspelled_role_still_lists_the_candidate(self, session, repository, query):
        assert _names(session, repository.id, query) == ["Grace Hyphen"]

    def test_a_misspelling_reaches_a_multi_version_candidate(self, session, repository):
        assert _names(session, repository.id, "bakend enginer") == ["Vera Versioned"]

    def test_an_unrelated_query_returns_nothing(self, session, repository):
        assert _names(session, repository.id, "plumber") == []

    def test_an_exact_match_is_not_widened(self, session, repository):
        # "devops engineer" matches Dana exactly, so the fuzzy pass never runs and
        # near neighbours are not dragged in.
        assert _names(session, repository.id, "devops engineer") == ["Dana Ops"]

    def test_fuzzy_results_still_respect_other_filters(self, session, repository):
        _, total = candidate_service.list_candidates(
            session, repository.id, query="fulstak", min_years=99
        )
        assert total == 0

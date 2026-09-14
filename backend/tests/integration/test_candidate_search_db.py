"""Free-text candidate search against Postgres.

The SQL half of the search cannot be unit tested: the bug that made a role search
match nothing was an aggregate subquery that silently correlated to the wrong table,
which only shows up once the statement actually runs.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, select, text

from app.core.config import settings
from app.core.security import hash_password
from app.db.models import (
    Candidate,
    CandidateProfile,
    CandidateSkill,
    Resume,
    ResumeStatus,
    Skill,
    User,
)
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


class TestSkillSearchUsesTheTaxonomy:
    """A recruiter typing "React" means the technology, not the exact string.

    The parser labels the same skill differently depending on how each resume wrote
    it — "React" on one, "React.js" on the next. Matching only the raw profile array
    meant a search for React found six candidates in one repository and none in
    another, purely by resume wording, and the assistant reported that as "no
    candidates with React experience".
    """

    def _with_skills(self, session, owner_id, name, skills):
        candidate = _candidate(session, owner_id, name, "Engineer", "Engineer")
        profile = session.execute(
            select(CandidateProfile)
            .where(CandidateProfile.candidate_id == candidate.id)
            .order_by(CandidateProfile.version.desc())
        ).scalars().first()
        profile.skills = skills
        session.commit()
        return candidate

    def _canonical(self, session, candidate_id, canonical_name, slug, aliases):
        skill = session.execute(
            select(Skill).where(Skill.slug == slug)
        ).scalars().first()
        if skill is None:
            skill = Skill(canonical_name=canonical_name, slug=slug, aliases=aliases)
            session.add(skill)
            session.flush()
        session.add(CandidateSkill(candidate_id=candidate_id, skill_id=skill.id))
        session.commit()

    def test_an_alias_in_the_profile_is_found_by_the_canonical_name(
        self, session, recruiter
    ):
        candidate = self._with_skills(session, recruiter.id, "Alias Holder", ["React.js"])
        self._canonical(session, candidate.id, "React", "react", ["react.js"])

        rows, total = candidate_service.list_candidates(session, recruiter.id, skill="React")

        assert total == 1, "searching the canonical name missed a resume that used an alias"
        assert rows[0].full_name == "Alias Holder"

    def test_the_canonical_name_in_the_profile_is_found_by_an_alias(
        self, session, recruiter
    ):
        candidate = self._with_skills(session, recruiter.id, "Canonical Holder", ["React"])
        self._canonical(session, candidate.id, "React", "react", ["react.js"])

        rows, total = candidate_service.list_candidates(
            session, recruiter.id, skill="React.js"
        )

        assert total == 1
        assert rows[0].full_name == "Canonical Holder"

    def test_a_profile_with_no_taxonomy_edge_is_still_found_literally(
        self, session, recruiter
    ):
        """The literal match is kept alongside the taxonomy one: a profile parsed
        before the taxonomy existed has no canonical edge and must stay findable."""
        self._with_skills(session, recruiter.id, "Untagged", ["Kubernetes"])

        rows, total = candidate_service.list_candidates(
            session, recruiter.id, skill="kubernetes"
        )

        assert total == 1
        assert rows[0].full_name == "Untagged"

    def test_an_unrelated_skill_still_does_not_match(self, session, recruiter):
        candidate = self._with_skills(session, recruiter.id, "React Dev", ["React.js"])
        self._canonical(session, candidate.id, "React", "react", ["react.js"])

        _rows, total = candidate_service.list_candidates(session, recruiter.id, skill="COBOL")

        assert total == 0


class TestLocationFilter:
    """`candidates.location` was populated and unreachable — the assistant had to
    tell a recruiter it could not filter by location at all."""

    def _at(self, session, owner_id, name, location):
        candidate = _candidate(session, owner_id, name, "Engineer", "Engineer")
        candidate.location = location
        session.commit()
        return candidate

    def test_a_city_matches_as_a_substring(self, session, recruiter):
        self._at(session, recruiter.id, "Chennai Person", "Chennai, India")
        self._at(session, recruiter.id, "Berlin Person", "Berlin, Germany")

        rows, total = candidate_service.list_candidates(
            session, recruiter.id, location="Chennai"
        )

        assert total == 1, "a stored 'Chennai, India' must match a search for 'Chennai'"
        assert rows[0].full_name == "Chennai Person"

    def test_matching_is_case_insensitive(self, session, recruiter):
        self._at(session, recruiter.id, "Chennai Person", "Chennai, India")

        _rows, total = candidate_service.list_candidates(
            session, recruiter.id, location="chennai"
        )

        assert total == 1

    def test_a_candidate_with_no_location_is_excluded_not_included(
        self, session, recruiter
    ):
        """Half this repository has no location. They are unknown, not elsewhere —
        which is why the tool description tells the assistant not to use this filter
        to prove nobody is in a city."""
        self._at(session, recruiter.id, "Chennai Person", "Chennai, India")
        _candidate(session, recruiter.id, "No Location", "Engineer", "Engineer")

        _rows, total = candidate_service.list_candidates(
            session, recruiter.id, location="Chennai"
        )

        assert total == 1

    def test_location_combines_with_skill(self, session, recruiter):
        here = self._at(session, recruiter.id, "Chennai React", "Chennai, India")
        profile = session.execute(
            select(CandidateProfile)
            .where(CandidateProfile.candidate_id == here.id)
            .order_by(CandidateProfile.version.desc())
        ).scalars().first()
        profile.skills = ["React"]
        elsewhere = self._at(session, recruiter.id, "Berlin React", "Berlin, Germany")
        other = session.execute(
            select(CandidateProfile)
            .where(CandidateProfile.candidate_id == elsewhere.id)
            .order_by(CandidateProfile.version.desc())
        ).scalars().first()
        other.skills = ["React"]
        session.commit()

        rows, total = candidate_service.list_candidates(
            session, recruiter.id, skill="React", location="Chennai"
        )

        assert total == 1
        assert rows[0].full_name == "Chennai React"

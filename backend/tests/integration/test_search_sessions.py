"""Search-session paging against a real Postgres (architecture doc §13).

These need a database because the whole point of a session is persisted state: the
``shown`` flag, the exclusion of already-seen candidates, and recruiter scoping are
all properties of stored rows, not of any in-memory object.
"""

from __future__ import annotations

import uuid

import pytest
from seed_data import SAMPLE_CANDIDATES, SAMPLE_JOB_DESCRIPTION
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.security import hash_password
from app.db.models import Resume, SearchSessionStatus, User
from app.services import job_service, matching_service, resume_service, search_service
from app.services.search_service import SearchSessionNotFound


def _database_available() -> bool:
    try:
        engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 2},
        )
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            connection.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).one()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _database_available(),
    reason=(
        "Postgres with pgvector is not reachable — run "
        "`docker compose up -d && alembic upgrade head`"
    ),
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
        name="Search Session Test",
        email=f"ss-{uuid.uuid4().hex[:12]}@example.com",
        password_hash=hash_password("test-password"),
    )
    session.add(user)
    session.commit()
    yield user
    session.delete(user)
    session.commit()


@pytest.fixture
def screened(session, owner):
    """A recruiter with an ingested pool and one completed screening."""
    for _external_id, _name, resume_text in SAMPLE_CANDIDATES[:6]:
        resume: Resume = resume_service.accept_upload(
            session,
            owner_id=owner.id,
            filename=f"{uuid.uuid4().hex[:8]}.txt",
            content_type="text/plain",
            data=resume_text.encode("utf-8"),
        )
        resume_service.process_resume(session, resume.id)

    job = job_service.create_job(
        session, owner_id=owner.id, title=None, description=SAMPLE_JOB_DESCRIPTION
    )
    screening = matching_service.create_screening(session, job=job)
    matching_service.run_screening(session, screening.id)
    session.refresh(screening)
    return job, screening


class TestSearchSessions:
    def test_session_snapshots_the_whole_ranking(self, session, owner, screened):
        _job, screening = screened
        search = search_service.create_session(
            session, recruiter_id=owner.id, screening=screening, page_size=2
        )
        page = search_service.current_page(session, search)
        assert page.total > 0
        # Nothing is shown until a page is requested.
        assert page.results == []
        assert page.remaining == page.total

    def test_paging_never_repeats_a_candidate(self, session, owner, screened):
        _job, screening = screened
        search = search_service.create_session(
            session, recruiter_id=owner.id, screening=screening, page_size=2
        )

        seen: list[uuid.UUID] = []
        for _ in range(10):
            page = search_service.next_page(session, search)
            if not page.results:
                break
            seen.extend(row.candidate_id for row in page.results)

        assert seen, "expected at least one page of results"
        assert len(seen) == len(set(seen)), "a candidate was shown twice"

    def test_remaining_count_reflects_the_page_just_returned(self, session, owner, screened):
        """Regression: autoflush is off, so counts were one page stale."""
        _job, screening = screened
        search = search_service.create_session(
            session, recruiter_id=owner.id, screening=screening, page_size=2
        )
        total = search_service.current_page(session, search).total

        page = search_service.next_page(session, search)
        assert page.remaining == total - len(page.results)

    def test_current_page_does_not_advance_the_cursor(self, session, owner, screened):
        _job, screening = screened
        search = search_service.create_session(
            session, recruiter_id=owner.id, screening=screening, page_size=2
        )
        first = search_service.next_page(session, search)
        ids = [row.candidate_id for row in first.results]

        again = search_service.current_page(session, search)
        assert [row.candidate_id for row in again.results] == ids
        assert again.remaining == first.remaining

    def test_exhausted_session_returns_empty_not_error(self, session, owner, screened):
        _job, screening = screened
        search = search_service.create_session(
            session, recruiter_id=owner.id, screening=screening, page_size=50
        )
        search_service.next_page(session, search)
        tail = search_service.next_page(session, search)

        assert tail.results == []
        assert tail.remaining == 0
        assert search.status == SearchSessionStatus.EXHAUSTED.value

    def test_another_recruiter_cannot_read_the_session(self, session, owner, screened):
        """Cross-tenant read must fail as 'not found', per doc §6."""
        _job, screening = screened
        search = search_service.create_session(
            session, recruiter_id=owner.id, screening=screening
        )

        intruder = User(
            name="Intruder",
            email=f"x-{uuid.uuid4().hex[:12]}@example.com",
            password_hash=hash_password("test-password"),
        )
        session.add(intruder)
        session.commit()
        try:
            with pytest.raises(SearchSessionNotFound):
                search_service.get_session(session, search.id, intruder.id)
        finally:
            session.delete(intruder)
            session.commit()

    def test_cannot_open_a_session_over_another_recruiters_screening(
        self, session, owner, screened
    ):
        _job, screening = screened
        intruder = User(
            name="Intruder Two",
            email=f"x2-{uuid.uuid4().hex[:12]}@example.com",
            password_hash=hash_password("test-password"),
        )
        session.add(intruder)
        session.commit()
        try:
            with pytest.raises(SearchSessionNotFound):
                search_service.create_session(
                    session, recruiter_id=intruder.id, screening=screening
                )
        finally:
            session.delete(intruder)
            session.commit()

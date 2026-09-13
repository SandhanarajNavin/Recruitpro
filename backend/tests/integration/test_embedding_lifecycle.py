"""Embeddings must not outlive what they describe.

``embeddings.owner_id`` is polymorphic, so it carries no foreign key and nothing
cascades to it. Before migration 0010, 8,265 of 8,422 rows were vectors for
candidates and jobs that had been deleted — 86 MB where the live data was under 2 MB,
all of it inside the HNSW index that every screening's ANN scan walks.

The fix is database triggers, and the reason it has to be triggers is the case
tested last here: candidates and jobs go away via ``ON DELETE CASCADE`` from
``users``, entirely inside the database, so no application-level hook would run.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, func, select, text

from app.ai.embeddings import get_embedder
from app.core.config import settings
from app.core.security import hash_password
from app.db.models import (
    Candidate,
    Embedding,
    EmbeddingOwner,
    Job,
    Resume,
    ResumeChunk,
    ResumeStatus,
    User,
)


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


# Reachability only. An earlier version of this guard also checked that the trigger
# existed, which meant that removing the fix made these tests skip instead of fail —
# a test that disappears along with its subject protects nothing.
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
        name="Lifecycle Test",
        email=f"lifecycle-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password("test-password"),
    )
    session.add(user)
    session.commit()
    yield user
    # Deliberately unconditional: several tests delete the user themselves, and a
    # second delete of an absent row is a no-op.
    session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
    session.commit()


def _vector(session, owner_type: str, owner_id) -> None:
    embedder = get_embedder()
    session.add(
        Embedding(
            owner_type=owner_type,
            owner_id=owner_id,
            vector=embedder.embed("some text"),
            model=embedder.model,
            dim=embedder.dim,
        )
    )
    session.commit()


def _count(session, owner_type: str, owner_id) -> int:
    return session.execute(
        select(func.count())
        .select_from(Embedding)
        .where(Embedding.owner_type == owner_type, Embedding.owner_id == owner_id)
    ).scalar()


class TestDeletingAnOwnerRemovesItsVector:
    def test_candidate(self, session, owner):
        candidate = Candidate(owner_id=owner.id, full_name="Doomed Person")
        session.add(candidate)
        session.commit()
        _vector(session, EmbeddingOwner.CANDIDATE.value, candidate.id)
        assert _count(session, EmbeddingOwner.CANDIDATE.value, candidate.id) == 1

        candidate_id = candidate.id
        session.delete(candidate)
        session.commit()

        assert _count(session, EmbeddingOwner.CANDIDATE.value, candidate_id) == 0

    def test_job(self, session, owner):
        job = Job(owner_id=owner.id, title="Doomed Role", description="x")
        session.add(job)
        session.commit()
        _vector(session, EmbeddingOwner.JOB.value, job.id)

        job_id = job.id
        session.delete(job)
        session.commit()

        assert _count(session, EmbeddingOwner.JOB.value, job_id) == 0

    def test_resume_passage(self, session, owner):
        candidate = Candidate(owner_id=owner.id, full_name="Passage Person")
        session.add(candidate)
        session.flush()
        resume = Resume(
            candidate_id=candidate.id,
            storage_key=f"k/{uuid.uuid4().hex}",
            original_filename="r.txt",
            content_type="text/plain",
            size_bytes=1,
            status=ResumeStatus.READY.value,
            extracted_text="some text",
        )
        session.add(resume)
        session.flush()
        chunk = ResumeChunk(
            resume_id=resume.id, candidate_id=candidate.id, ordinal=0, text="some text"
        )
        session.add(chunk)
        session.commit()
        _vector(session, EmbeddingOwner.RESUME_CHUNK.value, chunk.id)

        chunk_id = chunk.id
        session.delete(chunk)
        session.commit()

        assert _count(session, EmbeddingOwner.RESUME_CHUNK.value, chunk_id) == 0

    def test_one_owners_vector_going_does_not_take_anothers(self, session, owner):
        first = Candidate(owner_id=owner.id, full_name="Goes")
        second = Candidate(owner_id=owner.id, full_name="Stays")
        session.add_all([first, second])
        session.commit()
        _vector(session, EmbeddingOwner.CANDIDATE.value, first.id)
        _vector(session, EmbeddingOwner.CANDIDATE.value, second.id)

        first_id, second_id = first.id, second.id
        session.delete(first)
        session.commit()

        assert _count(session, EmbeddingOwner.CANDIDATE.value, first_id) == 0
        assert _count(session, EmbeddingOwner.CANDIDATE.value, second_id) == 1


class TestTheCascadePath:
    def test_deleting_a_recruiter_clears_every_vector_they_owned(self, session, owner):
        """The case that made triggers necessary rather than a service-level hook.

        Candidates and jobs are removed by ON DELETE CASCADE from users, so the
        delete runs inside the database and no Python executes. This is also the
        path the test suite takes on teardown, which is where the 8,265 orphans
        came from.
        """
        candidate = Candidate(owner_id=owner.id, full_name="Cascaded Person")
        job = Job(owner_id=owner.id, title="Cascaded Role", description="x")
        session.add_all([candidate, job])
        session.commit()
        _vector(session, EmbeddingOwner.CANDIDATE.value, candidate.id)
        _vector(session, EmbeddingOwner.JOB.value, job.id)
        candidate_id, job_id = candidate.id, job.id

        session.delete(owner)
        session.commit()

        assert _count(session, EmbeddingOwner.CANDIDATE.value, candidate_id) == 0
        assert _count(session, EmbeddingOwner.JOB.value, job_id) == 0

    def test_a_bulk_delete_is_handled_in_one_statement(self, session, owner):
        """The trigger is statement-level with a transition table, so a cascade that
        removes many rows costs one DELETE against embeddings rather than N."""
        candidates = [Candidate(owner_id=owner.id, full_name=f"Bulk {i}") for i in range(12)]
        session.add_all(candidates)
        session.commit()
        for candidate in candidates:
            _vector(session, EmbeddingOwner.CANDIDATE.value, candidate.id)
        ids = [candidate.id for candidate in candidates]
        assert (
            session.execute(
                select(func.count())
                .select_from(Embedding)
                .where(Embedding.owner_id.in_(ids))
            ).scalar()
            == 12
        )

        session.execute(text("DELETE FROM candidates WHERE owner_id = :o"), {"o": owner.id})
        session.commit()

        assert (
            session.execute(
                select(func.count())
                .select_from(Embedding)
                .where(Embedding.owner_id.in_(ids))
            ).scalar()
            == 0
        )


class TestWhatIsDeliberatelyKept:
    def test_a_superseded_models_vector_survives_while_its_owner_does(self, session, owner):
        """Not an orphan. Retrieval only compares within one model, so a vector from
        a previous embedding model is inert — and keeping it is what lets you switch
        models back without re-embedding everything."""
        candidate = Candidate(owner_id=owner.id, full_name="Two Models")
        session.add(candidate)
        session.commit()
        embedder = get_embedder()
        session.add(
            Embedding(
                owner_type=EmbeddingOwner.CANDIDATE.value,
                owner_id=candidate.id,
                vector=embedder.embed("text"),
                model="some-previous-model",
                dim=embedder.dim,
            )
        )
        session.commit()

        assert _count(session, EmbeddingOwner.CANDIDATE.value, candidate.id) == 1

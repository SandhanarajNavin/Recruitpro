"""Passage-level retrieval over resume text.

Runs against the offline embedder, which conftest forces for the whole suite, so
these assert structure rather than semantic quality: what is indexed, who can see it,
that re-ingestion replaces rather than accumulates, and that a query matching nothing
returns nothing. Relevance is a property of the embedding model and is verified by
hand against the real one.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, func, select, text

from app.ai.retrieval import passage_search
from app.core.config import settings
from app.core.security import hash_password
from app.db.models import (
    Candidate,
    Embedding,
    EmbeddingOwner,
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
            connection.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).one()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _database_available(),
    reason="Postgres with pgvector is not reachable — run `docker compose up -d`",
)

ALICE_RESUME = """AISHA BELLO
Full-Stack Engineer

EXPERIENCE
Acme — Full-Stack Engineer (2019 - Present)
- Built an object detection pipeline using YOLOv5 for warehouse inventory
- Ran the Kubernetes migration for the checkout service

EDUCATION
BSc Computer Science
"""

BOB_RESUME = """MARCUS FELD
Senior Java Engineer

EXPERIENCE
Initech — Senior Java Engineer (2015 - Present)
- Maintained a Spring Boot payments gateway
"""


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
        name=f"Passage {label}",
        email=f"passage-{label}-{uuid.uuid4().hex[:10]}@example.com",
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


def _indexed(session, owner, name: str, body: str) -> tuple[Candidate, Resume]:
    candidate = Candidate(owner_id=owner.id, full_name=name)
    session.add(candidate)
    session.flush()
    resume = Resume(
        candidate_id=candidate.id,
        storage_key=f"k/{uuid.uuid4().hex}",
        original_filename=f"{name}.txt",
        content_type="text/plain",
        size_bytes=len(body),
        status=ResumeStatus.READY.value,
        extracted_text=body,
    )
    session.add(resume)
    session.flush()
    passage_search.index_resume(session, resume, candidate.id)
    session.commit()
    return candidate, resume


class TestIndexing:
    def test_a_resume_becomes_passages_with_vectors(self, session, alice):
        _candidate, resume = _indexed(session, alice, "Aisha Bello", ALICE_RESUME)

        chunks = session.execute(
            select(ResumeChunk).where(ResumeChunk.resume_id == resume.id)
        ).scalars().all()
        assert chunks, "no passages indexed"
        # Ordinals are document order, so retrieved passages can be shown in order.
        assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))

        vectors = session.execute(
            select(func.count())
            .select_from(Embedding)
            .where(
                Embedding.owner_type == EmbeddingOwner.RESUME_CHUNK.value,
                Embedding.owner_id.in_([chunk.id for chunk in chunks]),
            )
        ).scalar()
        assert vectors == len(chunks), "every passage needs exactly one vector"

    def test_reindexing_replaces_rather_than_accumulates(self, session, alice):
        """A re-ingested resume must not leave the previous version's passages to be
        retrieved as though they were current."""
        candidate, resume = _indexed(session, alice, "Aisha Bello", ALICE_RESUME)
        first = session.execute(
            select(func.count()).select_from(ResumeChunk).where(ResumeChunk.resume_id == resume.id)
        ).scalar()

        resume.extracted_text = BOB_RESUME
        passage_search.index_resume(session, resume, candidate.id)
        session.commit()

        chunks = session.execute(
            select(ResumeChunk).where(ResumeChunk.resume_id == resume.id)
        ).scalars().all()
        assert len(chunks) <= first + 1
        joined = " ".join(chunk.text for chunk in chunks)
        assert "Spring Boot" in joined
        assert "YOLOv5" not in joined, "stale passage survived a re-index"

    def test_the_old_vectors_go_with_the_old_passages(self, session, alice):
        # Nothing cascades from embeddings, so index_resume has to clean up itself.
        candidate, resume = _indexed(session, alice, "Aisha Bello", ALICE_RESUME)
        stale = [
            row[0]
            for row in session.execute(
                select(ResumeChunk.id).where(ResumeChunk.resume_id == resume.id)
            ).all()
        ]

        resume.extracted_text = BOB_RESUME
        passage_search.index_resume(session, resume, candidate.id)
        session.commit()

        left = session.execute(
            select(func.count())
            .select_from(Embedding)
            .where(
                Embedding.owner_type == EmbeddingOwner.RESUME_CHUNK.value,
                Embedding.owner_id.in_(stale),
            )
        ).scalar()
        assert left == 0, "orphaned passage vectors left behind"

    def test_a_resume_with_no_text_indexes_nothing_and_does_not_fail(self, session, alice):
        # Image-only PDFs reach here. The candidate stays screenable on their
        # parsed profile, so this is not an error.
        candidate = Candidate(owner_id=alice.id, full_name="Scanned Person")
        session.add(candidate)
        session.flush()
        resume = Resume(
            candidate_id=candidate.id,
            storage_key=f"k/{uuid.uuid4().hex}",
            original_filename="scan.pdf",
            content_type="application/pdf",
            size_bytes=1,
            status=ResumeStatus.READY.value,
            extracted_text="",
        )
        session.add(resume)
        session.flush()

        assert passage_search.index_resume(session, resume, candidate.id) == 0


class TestSearch:
    def test_a_query_finds_the_passage_that_contains_it(self, session, alice):
        _indexed(session, alice, "Aisha Bello", ALICE_RESUME)

        hits = passage_search.search(
            session, owner_id=alice.id, query="YOLOv5 object detection", limit=3
        )

        assert hits, "an exact term present in the resume was not retrieved"
        assert any("YOLOv5" in hit.text for hit in hits)
        # A passage is quoted to the recruiter, so it arrives attributed.
        assert hits[0].candidate_name == "Aisha Bello"

    def test_another_recruiters_passages_are_never_returned(self, session, alice, bob):
        """The strongest requirement here: a passage is raw resume text, so a leak
        hands one recruiter another's candidate verbatim."""
        _indexed(session, alice, "Aisha Bello", ALICE_RESUME)
        _indexed(session, bob, "Marcus Feld", BOB_RESUME)

        theirs = passage_search.search(
            session, owner_id=bob.id, query="YOLOv5 object detection", limit=10
        )

        assert all(hit.candidate_name == "Marcus Feld" for hit in theirs)

    def test_scoping_to_one_candidate(self, session, alice):
        aisha, _ = _indexed(session, alice, "Aisha Bello", ALICE_RESUME)
        _indexed(session, alice, "Marcus Feld", BOB_RESUME)

        hits = passage_search.search(
            session,
            owner_id=alice.id,
            query="engineer",
            candidate_id=aisha.id,
            limit=10,
            min_similarity=-1.0,
        )

        assert hits
        assert {hit.candidate_id for hit in hits} == {aisha.id}

    def test_an_empty_query_returns_nothing_without_embedding_it(self, session, alice):
        _indexed(session, alice, "Aisha Bello", ALICE_RESUME)
        assert passage_search.search(session, owner_id=alice.id, query="   ") == []

    def test_the_floor_keeps_unrelated_queries_empty(self, session, alice):
        """An ANN search always returns its limit, so without a floor a question
        about work nobody has done comes back with the least-unrelated passages and
        they read as answers."""
        _indexed(session, alice, "Aisha Bello", ALICE_RESUME)

        with_floor = passage_search.search(
            session, owner_id=alice.id, query="underwater basket weaving certification", limit=5
        )
        # -1.0, not 0.0: the offline embedder signs its hash buckets, so an
        # unrelated query scores slightly *negative* rather than zero.
        without_floor = passage_search.search(
            session,
            owner_id=alice.id,
            query="underwater basket weaving certification",
            limit=5,
            min_similarity=-1.0,
        )

        assert with_floor == []
        assert without_floor, "the floor is what removes these, not the query itself"
        assert all(hit.similarity < 0.03 for hit in without_floor)

    def test_archived_candidates_drop_out_of_passage_search(self, session, alice):
        candidate, _ = _indexed(session, alice, "Aisha Bello", ALICE_RESUME)
        candidate.status = "archived"
        session.commit()

        hits = passage_search.search(
            session, owner_id=alice.id, query="YOLOv5", limit=5, min_similarity=-1.0
        )
        assert hits == []

    def test_only_vectors_from_the_current_model_are_compared(self, session, alice):
        """Mixing embedding spaces produces nonsense similarity, so a passage
        embedded by a different model must be invisible rather than wrong."""
        _candidate, resume = _indexed(session, alice, "Aisha Bello", ALICE_RESUME)
        chunk_ids = [
            row[0]
            for row in session.execute(
                select(ResumeChunk.id).where(ResumeChunk.resume_id == resume.id)
            ).all()
        ]
        session.execute(
            text(
                "UPDATE embeddings SET model = 'some-other-model' "
                "WHERE owner_type = 'resume_chunk' AND owner_id = ANY(:ids)"
            ),
            {"ids": chunk_ids},
        )
        session.commit()

        hits = passage_search.search(
            session, owner_id=alice.id, query="YOLOv5", limit=5, min_similarity=-1.0
        )
        assert hits == []


# Six roles, each independently about procurement, so every passage of this resume
# matches a procurement query. Purpose-built: one candidate with many on-topic
# passages is exactly the shape that used to fill a whole page of results.
PROCUREMENT_HEAVY = "\n\n".join(
    f"Vendor Corp - Procurement Lead ({2010 + index} - {2011 + index})\n"
    "- Ran software procurement for the engineering org, negotiating vendor "
    "contracts and managing the annual license renewal cycle end to end.\n"
    "- Evaluated procurement tooling and led RFP scoring with the procurement "
    "committee across a portfolio of SaaS vendors."
    for index in range(6)
)

# One passage on the same subject: enough to match, not enough to outrank six.
PROCUREMENT_LIGHT = """DIYA RAO
Procurement Analyst

EXPERIENCE
Initech - Procurement Analyst (2020 - Present)
- Supported software procurement and vendor contract renewals for IT.
"""


class TestOnePassagePerCandidate:
    """`limit` counts people, not paragraphs.

    A resume chunks into several passages, so a candidate whose whole career is on
    topic matches most of them. Ranking passages alone let that one person occupy
    every slot, and the recruiter never saw the second-best candidate at all.
    """

    def test_one_strong_resume_cannot_fill_the_whole_page(self, session, alice):
        _indexed(session, alice, "Vikram Shetty", PROCUREMENT_HEAVY)
        _indexed(session, alice, "Diya Rao", PROCUREMENT_LIGHT)

        hits = passage_search.search(
            session,
            owner_id=alice.id,
            query="software procurement",
            limit=5,
            min_similarity=-1.0,
        )

        names = [hit.candidate_name for hit in hits]
        assert len(names) == len(set(names)), f"a candidate repeats in {names}"
        assert set(names) == {"Vikram Shetty", "Diya Rao"}

    def test_without_collapsing_one_candidate_takes_the_page(self, session, alice):
        """The behaviour being prevented, pinned so the fix cannot be quietly lost."""
        _indexed(session, alice, "Vikram Shetty", PROCUREMENT_HEAVY)
        _indexed(session, alice, "Diya Rao", PROCUREMENT_LIGHT)

        hits = passage_search.search(
            session,
            owner_id=alice.id,
            query="software procurement",
            limit=5,
            min_similarity=-1.0,
            one_per_candidate=False,
        )

        # Five slots, and only Vikram has five matching passages.
        vikram = sum(1 for hit in hits if hit.candidate_name == "Vikram Shetty")
        assert vikram >= 4, "expected one candidate to dominate an uncollapsed page"

    def test_scoping_to_one_candidate_still_returns_several_passages(self, session, alice):
        """Collapsing is ignored under `candidate_id`: "what did X say about Y" wants
        the passages, not the single best one."""
        vikram, _ = _indexed(session, alice, "Vikram Shetty", PROCUREMENT_HEAVY)

        hits = passage_search.search(
            session,
            owner_id=alice.id,
            query="software procurement",
            candidate_id=vikram.id,
            limit=5,
            min_similarity=-1.0,
        )

        assert len(hits) > 1
        assert {hit.candidate_id for hit in hits} == {vikram.id}


class TestMatchedCandidateCount:
    """"How many people" is not answerable from a capped list of passages."""

    def test_the_count_is_people_not_passages(self, session, alice):
        _indexed(session, alice, "Vikram Shetty", PROCUREMENT_HEAVY)
        _indexed(session, alice, "Diya Rao", PROCUREMENT_LIGHT)

        found = passage_search.search_passages(
            session,
            owner_id=alice.id,
            query="software procurement",
            limit=1,
            min_similarity=-1.0,
        )

        # One passage asked for, two people matched. The list length is a page size.
        assert len(found.passages) == 1
        assert found.matched_candidates == 2

    def test_a_count_the_window_contained_is_not_a_lower_bound(self, session, alice):
        _indexed(session, alice, "Diya Rao", PROCUREMENT_LIGHT)

        found = passage_search.search_passages(
            session,
            owner_id=alice.id,
            query="software procurement",
            limit=1,
            min_similarity=-1.0,
        )

        # One passage in the whole repository against a window of eight: the search
        # saw everything, so the count is a total.
        assert found.truncated is False
        assert found.matched_candidates == 1

    def test_a_full_window_marks_the_count_as_a_lower_bound(self, session, alice):
        """Ten passages against a window of eight — the search cannot have seen the
        whole repository, and the caller has to be told before quoting a total."""
        _indexed(session, alice, "Vikram Shetty", PROCUREMENT_HEAVY)
        _indexed(session, alice, "Nadia Rahman", PROCUREMENT_HEAVY)

        found = passage_search.search_passages(
            session,
            owner_id=alice.id,
            query="software procurement",
            limit=1,
            min_similarity=-1.0,
        )

        assert found.truncated is True

    def test_an_empty_query_reports_no_matches_rather_than_guessing(self, session, alice):
        _indexed(session, alice, "Diya Rao", PROCUREMENT_LIGHT)

        found = passage_search.search_passages(session, owner_id=alice.id, query="  ")

        assert found.passages == []
        assert found.matched_candidates == 0
        assert found.truncated is False


class TestHnswSearchBreadth:
    """An HNSW scan returns at most ef_search rows, so the walk must never be
    narrower than the window being fetched.

    Collapsing to one passage per candidate overfetches to several times `limit`,
    which at pgvector's default ef_search of 40 measured 50% recall and half the
    requested rows on 5,400 vectors. These pin the floor, not the plan — whether
    HNSW is chosen at all is the planner's cost decision.
    """

    def test_the_walk_is_widened_for_the_search(self, session, alice):
        _indexed(session, alice, "Diya Rao", PROCUREMENT_LIGHT)

        passage_search.search(
            session, owner_id=alice.id, query="software procurement", limit=5
        )

        # Readable only once a vector operation has loaded pgvector's library, which
        # the search above has just done — SHOW fails on a connection that has not.
        applied = int(session.execute(text("SHOW hnsw.ef_search")).scalar())
        assert applied >= settings.hnsw_ef_search

    def test_the_walk_is_never_narrower_than_the_window(self, session, alice):
        """A walk narrower than the number of rows requested cannot fill the request
        even before the WHERE clause discards anything."""
        _indexed(session, alice, "Diya Rao", PROCUREMENT_LIGHT)

        passage_search._widen_hnsw_walk(session, window=settings.hnsw_ef_search * 3)
        session.execute(text("SELECT '[1,0,0]'::vector <=> '[0,1,0]'::vector"))

        applied = int(session.execute(text("SHOW hnsw.ef_search")).scalar())
        assert applied == settings.hnsw_ef_search * 3

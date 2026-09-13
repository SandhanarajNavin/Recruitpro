"""End-to-end tests against a real Postgres with pgvector.

Skipped automatically when the database is unreachable, so `pytest` stays green on a
machine with no Docker. Bring the stack up and these run:

    docker compose up -d
    alembic upgrade head
    pytest

What they prove is the part unit tests cannot: that the retrieval SQL is valid, that
ingestion persists a usable profile and vector, and that a screening produces a ranked
shortlist with evidence.
"""

from __future__ import annotations

import uuid

import pytest
from seed_data import SAMPLE_CANDIDATES, SAMPLE_JOB_DESCRIPTION
from sqlalchemy import create_engine, select, text

from app.core.config import settings
from app.core.security import hash_password
from app.db.models import (
    Candidate,
    CandidateProfile,
    Embedding,
    Resume,
    ResumeStatus,
    ScreeningResult,
    User,
)
from app.db.models.embedding import EmbeddingOwner
from app.db.models.screening import ScreeningStatus
from app.services import job_service, matching_service, resume_service


def _database_available() -> bool:
    try:
        # Short connect timeout: with Docker Desktop half-started, port 5432 can
        # accept a TCP connection and then never answer, which otherwise stalls the
        # whole test session on a machine that has no database at all.
        engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 2},
        )
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            # Without the extension the vector column type does not exist, so the
            # migration has not been applied — treat that as unavailable.
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
    """A throwaway recruiter, torn down with everything they own."""
    user = User(
        name="Integration Test",
        email=f"it-{uuid.uuid4().hex[:12]}@example.com",
        password_hash=hash_password("test-password"),
    )
    session.add(user)
    session.commit()
    yield user
    session.delete(user)  # cascades to candidates, jobs, screenings
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
        session.refresh(resume)
        resumes.append(resume)
    return resumes


class TestIngestion:
    def test_ingests_a_resume_into_a_usable_profile_and_vector(self, session, owner):
        resume = _ingest(session, owner, 1)[0]

        assert resume.status == ResumeStatus.READY.value, resume.error
        assert resume.extracted_text and "Northwind Payments" in resume.extracted_text

        profile = session.execute(
            select(CandidateProfile).where(CandidateProfile.candidate_id == resume.candidate_id)
        ).scalars().one()
        assert {"TypeScript", "PostgreSQL", "Kubernetes"} <= set(profile.skills)
        assert float(profile.total_years_experience) >= 8

        embedding = session.execute(
            select(Embedding).where(
                Embedding.owner_type == EmbeddingOwner.CANDIDATE.value,
                Embedding.owner_id == resume.candidate_id,
            )
        ).scalars().one()
        assert embedding.dim == settings.embedding_dim
        assert len(embedding.vector) == settings.embedding_dim

    def test_reingesting_the_same_bytes_does_not_duplicate_the_candidate(self, session, owner):
        _external_id, name, resume_text = SAMPLE_CANDIDATES[0]
        payload = resume_text.encode("utf-8")

        first = resume_service.accept_upload(
            session, owner_id=owner.id, filename="a.txt", content_type="text/plain", data=payload
        )
        second = resume_service.accept_upload(
            session, owner_id=owner.id, filename="b.txt", content_type="text/plain", data=payload
        )
        assert first.id == second.id, "checksum match should return the existing resume"

        count = session.execute(
            select(Candidate).where(Candidate.owner_id == owner.id)
        ).scalars().all()
        assert len(count) == 1

    def test_reprocessing_creates_a_new_profile_version(self, session, owner):
        resume = _ingest(session, owner, 1)[0]
        resume_service.process_resume(session, resume.id)

        versions = session.execute(
            select(CandidateProfile.version)
            .where(CandidateProfile.candidate_id == resume.candidate_id)
            .order_by(CandidateProfile.version)
        ).scalars().all()
        assert versions == [1, 2], "a re-parse must version, not overwrite"

        # Still exactly one vector — retrieval must never see two per candidate.
        vectors = session.execute(
            select(Embedding).where(Embedding.owner_id == resume.candidate_id)
        ).scalars().all()
        assert len(vectors) == 1
        assert vectors[0].version == 2


class TestScreening:
    def test_full_funnel_produces_a_ranked_shortlist(self, session, owner):
        _ingest(session, owner, 10)

        job = job_service.create_job(
            session,
            owner_id=owner.id,
            title="Senior Full-Stack Engineer, Payments Platform",
            description=SAMPLE_JOB_DESCRIPTION,
        )
        assert job.requirement is not None
        required = {entry["skill"] for entry in job.requirement.required_skills}
        assert {"TypeScript", "PostgreSQL"} <= required

        screening = matching_service.create_screening(session, job=job)
        matching_service.run_screening(session, screening.id)
        session.refresh(screening)

        assert screening.status == ScreeningStatus.COMPLETED.value, screening.error
        assert screening.pool_size == 10
        assert screening.retrieved_count > 0
        assert screening.evaluated_count > 0

        results = matching_service.get_results(session, screening.id)
        assert results, "screening produced no results"

        # Ranks are dense, ordered, and scores descend with rank.
        assert [row.rank for row in results] == list(range(1, len(results) + 1))
        scores = [float(row.composite_score) for row in results]
        assert scores == sorted(scores, reverse=True)

        shortlist = [row for row in results if row.shortlisted]
        assert len(shortlist) == min(settings.shortlist_size, len(results))

        top = shortlist[0]
        assert top.subscores, "no score breakdown persisted"
        # The composite must equal the sum of the stored contributions — the audit
        # trail is only worth anything if it reconstructs the number.
        contributions = sum(entry["contribution"] for entry in top.subscores.values())
        assert abs(float(top.composite_score) - contributions) < 0.05

        assert top.explanation is not None
        assert top.explanation.why_match
        assert top.explanation.why_not, "gap section must never be empty"

    def test_payments_fullstack_outranks_the_frontend_only_candidate(self, session, owner):
        _ingest(session, owner, 10)
        job = job_service.create_job(
            session,
            owner_id=owner.id,
            title="Senior Full-Stack Engineer, Payments Platform",
            description=SAMPLE_JOB_DESCRIPTION,
        )
        screening = matching_service.create_screening(session, job=job)
        matching_service.run_screening(session, screening.id)

        results = matching_service.get_results(session, screening.id)
        names = {
            session.get(Candidate, row.candidate_id).full_name: row.rank for row in results
        }

        assert "Priya Raghunathan" in names, "the strongest candidate was filtered out"
        if "Mei Lin Chow" in names:
            assert names["Priya Raghunathan"] < names["Mei Lin Chow"]
        if "Grace Lindqvist" in names:
            assert names["Priya Raghunathan"] < names["Grace Lindqvist"]

    def test_rerunning_a_job_creates_a_second_independent_screening(self, session, owner):
        _ingest(session, owner, 3)
        job = job_service.create_job(
            session, owner_id=owner.id, title="Role", description=SAMPLE_JOB_DESCRIPTION
        )

        first = matching_service.create_screening(session, job=job)
        matching_service.run_screening(session, first.id)
        second = matching_service.create_screening(session, job=job)
        matching_service.run_screening(session, second.id)

        assert first.id != second.id
        first_results = matching_service.get_results(session, first.id)
        second_results = matching_service.get_results(session, second.id)
        assert len(first_results) == len(second_results)
        # Deterministic engine, same pool: the ranking must reproduce exactly.
        assert [float(r.composite_score) for r in first_results] == [
            float(r.composite_score) for r in second_results
        ]

    def test_screening_never_mutates_the_candidate_layer(self, session, owner):
        """The repository is read-only during a screening (architecture doc §2)."""
        _ingest(session, owner, 5)
        job = job_service.create_job(
            session, owner_id=owner.id, title="Role", description=SAMPLE_JOB_DESCRIPTION
        )

        before = {
            (row.id, row.version, row.updated_at)
            for row in session.execute(
                select(CandidateProfile).join(Candidate).where(Candidate.owner_id == owner.id)
            ).scalars().all()
        }

        screening = matching_service.create_screening(session, job=job)
        matching_service.run_screening(session, screening.id)
        session.expire_all()

        after = {
            (row.id, row.version, row.updated_at)
            for row in session.execute(
                select(CandidateProfile).join(Candidate).where(Candidate.owner_id == owner.id)
            ).scalars().all()
        }
        assert before == after, "a screening must not write to candidate profiles"

    def test_empty_repository_completes_with_no_shortlist(self, session, owner):
        job = job_service.create_job(
            session, owner_id=owner.id, title="Role", description=SAMPLE_JOB_DESCRIPTION
        )
        screening = matching_service.create_screening(session, job=job)
        matching_service.run_screening(session, screening.id)
        session.refresh(screening)

        # No candidates is a normal outcome, not a failure.
        assert screening.status == ScreeningStatus.COMPLETED.value, screening.error
        assert screening.pool_size == 0
        assert matching_service.get_results(session, screening.id) == []


class TestRetrievalFilters:
    def test_hard_filter_relaxes_when_it_would_empty_the_pool(self, session, owner):
        """A gate that excludes everyone is nearly always a parsing artefact."""
        _ingest(session, owner, 3)
        job = job_service.create_job(
            session, owner_id=owner.id, title="Role", description=SAMPLE_JOB_DESCRIPTION
        )
        # Force an unsatisfiable gate.
        job.requirement.hard_filters = {
            "must_have_skills": ["Fortran", "COBOL"],
            "min_years": 40,
        }
        session.commit()

        screening = matching_service.create_screening(session, job=job)
        matching_service.run_screening(session, screening.id)
        session.refresh(screening)

        assert screening.status == ScreeningStatus.COMPLETED.value, screening.error
        assert screening.degradations, "relaxing the gates must be reported to the recruiter"
        # Asserted on substance rather than phrasing: the note must name the
        # requirement that was dropped, so the recruiter can see what the ranking
        # no longer guarantees.
        note = " ".join(screening.degradations)
        assert "Fortran" in note and "40+ years" in note
        assert matching_service.get_results(session, screening.id), "should still rank someone"

    def test_min_years_gate_excludes_the_junior_candidate(self, session, owner):
        _ingest(session, owner, 10)
        job = job_service.create_job(
            session, owner_id=owner.id, title="Role", description=SAMPLE_JOB_DESCRIPTION
        )
        job.requirement.hard_filters = {"must_have_skills": [], "min_years": 8}
        session.commit()

        screening = matching_service.create_screening(session, job=job)
        matching_service.run_screening(session, screening.id)

        results = matching_service.get_results(session, screening.id)
        included = {session.get(Candidate, row.candidate_id).full_name for row in results}
        assert "Grace Lindqvist" not in included, "2-year candidate passed an 8-year gate"


class TestOverrides:
    def test_override_is_additive_and_leaves_the_score_alone(self, session, owner):
        _ingest(session, owner, 3)
        job = job_service.create_job(
            session, owner_id=owner.id, title="Role", description=SAMPLE_JOB_DESCRIPTION
        )
        screening = matching_service.create_screening(session, job=job)
        matching_service.run_screening(session, screening.id)

        result = matching_service.get_results(session, screening.id)[0]
        original_score = float(result.composite_score)
        original_recommendation = result.recommendation

        result.override_recommendation = "pass"
        result.override_note = "Withdrew from the process."
        result.override_by = owner.id
        session.commit()

        reloaded = session.get(ScreeningResult, result.id)
        assert float(reloaded.composite_score) == original_score
        assert reloaded.recommendation == original_recommendation
        assert reloaded.override_recommendation == "pass"

    def test_the_override_endpoint_returns_the_updated_row(self, session, owner):
        """Through HTTP, not the model.

        The test above writes the columns directly, so it passed while the endpoint
        itself returned a 500: it handed a plain name string to a serialiser that
        expects the candidate's full identity. Anything that only touches the ORM
        cannot see that, hence this one going over the wire.
        """
        from fastapi.testclient import TestClient

        from app.main import app

        _ingest(session, owner, 3)
        job = job_service.create_job(
            session, owner_id=owner.id, title="Role", description=SAMPLE_JOB_DESCRIPTION
        )
        screening = matching_service.create_screening(session, job=job)
        matching_service.run_screening(session, screening.id)
        result = matching_service.get_results(session, screening.id)[0]
        scored = float(result.composite_score)

        client = TestClient(app)
        token = client.post(
            "/api/v1/auth/login",
            json={"email": owner.email, "password": "test-password"},
        ).json()["access_token"]

        response = client.post(
            f"/api/v1/screenings/{screening.id}/results/{result.candidate_id}/override",
            json={"recommendation": "interview", "note": "Strong referral."},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["effective_recommendation"] == "interview"
        assert body["override_note"] == "Strong referral."
        # The identity the row needs to re-render, not just a name.
        assert body["name"] == session.get(Candidate, result.candidate_id).full_name
        assert body["candidate_id"] == str(result.candidate_id)
        assert body["role"] is not None and body["years"] is not None
        # An override annotates the decision; it must not restate the score.
        assert body["composite_score"] == pytest.approx(scored)
        assert body["recommendation"] == result.recommendation


class TestDuplicateEmailMerge:
    """Two resumes for the same person, matched on email (doc §22).

    Regression: the merge repointed ``resume.candidate_id`` but left the resume in
    the provisional candidate's ORM collection. Deleting that candidate cascaded
    ("all, delete-orphan") to the resume, and the profile insert immediately after
    failed with a foreign-key violation — the second upload was lost and marked
    FAILED, with the candidate stranded under its filename as a name.
    """

    #: The sample resumes carry no email address, so identity resolution never
    #: fires on them. Merging is keyed on an exact email match, so the fixture
    #: has to contain one.
    RESUME = """Dana Whitfield
Senior Backend Engineer
dana.whitfield@example.com | +1 555 0100

EXPERIENCE
Acme Payments - Senior Backend Engineer (2018 - Present)
- Built settlement services in Python, FastAPI and PostgreSQL
- Ran Kafka pipelines and Kubernetes deployments on AWS
- Cut reconciliation errors by 60% across the ledger

Northwind Ltd - Backend Engineer (2015 - 2018)
- Django services, Redis caching, Docker and Terraform

EDUCATION
B.E. Computer Science
"""

    def test_second_resume_merges_instead_of_failing(self, session, owner):
        resume_text = self.RESUME

        first = resume_service.accept_upload(
            session,
            owner_id=owner.id,
            filename="first.txt",
            content_type="text/plain",
            data=resume_text.encode("utf-8"),
        )
        resume_service.process_resume(session, first.id)

        first_resume = session.get(Resume, first.id)
        assert first_resume is not None
        assert first_resume.status == ResumeStatus.READY.value
        original_candidate_id = first_resume.candidate_id

        # Same person, different bytes, so the checksum short-circuit does not apply.
        second = resume_service.accept_upload(
            session,
            owner_id=owner.id,
            filename="second.txt",
            content_type="text/plain",
            data=(resume_text + "\n\nAdditional note: available immediately.").encode("utf-8"),
        )
        resume_service.process_resume(session, second.id)

        second_resume = session.get(Resume, second.id)
        assert second_resume is not None, "the merge deleted the resume row"
        assert second_resume.status == ResumeStatus.READY.value, second_resume.error
        assert second_resume.candidate_id == original_candidate_id, "did not merge"
        assert second_resume.version == 2, "version should advance on merge"

        # One person, not two.
        people = session.execute(
            select(Candidate).where(Candidate.owner_id == owner.id)
        ).scalars().all()
        assert len(people) == 1

        # And the merged profile exists rather than being rolled back.
        profiles = session.execute(
            select(CandidateProfile).where(
                CandidateProfile.candidate_id == original_candidate_id
            )
        ).scalars().all()
        assert len(profiles) == 2

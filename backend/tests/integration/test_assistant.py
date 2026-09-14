"""Recruiter assistant: tool behaviour and tenant isolation.

The model is never invoked here — these test the tools directly. What matters is not
what the assistant *says* but what the tools will do when it calls them, and that is
deterministic and cheap to assert.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, select, text

from app.agent.tools import ToolError, build_toolset
from app.core.config import settings
from app.core.security import hash_password
from app.db.models import (
    Application,
    ApplicationStage,
    AuditEvent,
    Candidate,
    Resume,
    User,
)
from app.services import application_service, job_service, matching_service, resume_service


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

RESUME = (
    "Wilhelmina Okonkwo\n"
    "Staff Platform Engineer\n"
    "will.okonkwo@example.com\n\n"
    "EXPERIENCE\n"
    "Globex - Staff Platform Engineer (2016 - Present)\n"
    "- Kubernetes, Terraform, AWS, Go, PostgreSQL at scale\n"
)

JOB = (
    "Senior Platform Engineer. We need someone with deep Kubernetes and Terraform "
    "experience to own our AWS estate, with at least five years in infrastructure "
    "and a track record of reliability work on production systems."
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
        name=f"Assistant Test {label}",
        email=f"asst-{label}-{uuid.uuid4().hex[:10]}@example.com",
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


@pytest.fixture
def alice_tools(session, alice):
    return {tool.__name__: tool for tool in build_toolset(session, alice)}


@pytest.fixture
def alice_candidate(session, alice) -> Candidate:
    resume: Resume = resume_service.accept_upload(
        session,
        owner_id=alice.id,
        filename="wilhelmina.txt",
        content_type="text/plain",
        data=RESUME.encode("utf-8"),
    )
    resume_service.process_resume(session, resume.id)
    stored = session.get(Resume, resume.id)
    return session.get(Candidate, stored.candidate_id)


class TestToolSchema:
    def test_no_tool_accepts_a_recruiter_id(self, alice_tools):
        """Identity comes from the closure. A tool that took it as a parameter would
        let a crafted prompt read another tenant."""
        import inspect

        for name, tool in alice_tools.items():
            params = set(inspect.signature(tool).parameters)
            leaked = params & {"recruiter_id", "owner_id", "user_id", "owner"}
            assert not leaked, f"{name} exposes identity parameters: {leaked}"

    def test_annotations_resolve_to_real_types(self, alice_tools):
        """Regression: `from __future__ import annotations` turned every hint into a
        string, and the SDK built a broken schema — every tool taking a parameter
        silently failed while zero-argument tools worked."""
        import inspect

        for name, tool in alice_tools.items():
            for param in inspect.signature(tool).parameters.values():
                assert not isinstance(param.annotation, str), (
                    f"{name}.{param.name} annotation is a string, not a type"
                )

    def test_every_tool_is_documented(self, alice_tools):
        """Docstrings become the tool descriptions the model reads."""
        for name, tool in alice_tools.items():
            assert (tool.__doc__ or "").strip(), f"{name} has no docstring"


class TestIsolation:
    def test_cannot_read_another_recruiters_candidate(
        self, session, bob, alice_candidate
    ):
        bob_tools = {tool.__name__: tool for tool in build_toolset(session, bob)}
        with pytest.raises(ToolError):
            bob_tools["get_candidate"](candidate=str(alice_candidate.id))

    def test_listing_shows_only_your_own(self, session, bob, alice_candidate):
        bob_tools = {tool.__name__: tool for tool in build_toolset(session, bob)}
        assert bob_tools["list_candidates"]()["total_matching"] == 0

    def test_owner_sees_their_own(self, alice_tools, alice_candidate):
        result = alice_tools["list_candidates"]()
        assert result["total_matching"] == 1
        assert result["candidates"][0]["candidate_id"] == str(alice_candidate.id)

    def test_cannot_override_on_another_recruiters_screening(self, session, bob):
        bob_tools = {tool.__name__: tool for tool in build_toolset(session, bob)}
        with pytest.raises(ToolError):
            bob_tools["set_recommendation"](
                screening_id=str(uuid.uuid4()),
                candidate_id=str(uuid.uuid4()),
                recommendation="interview",
            )


class TestReads:
    def test_get_candidate_returns_the_profile(self, alice_tools, alice_candidate):
        detail = alice_tools["get_candidate"](candidate=str(alice_candidate.id))
        assert detail["name"] == alice_candidate.full_name
        assert isinstance(detail["skills"], list)

    def test_get_candidate_accepts_a_name(self, alice_tools, alice_candidate):
        """Models do not reproduce UUIDs reliably — a real transcript had the
        assistant read a candidate, then pass two different fabricated ids for them
        on the next two turns. A name needs no recall."""
        detail = alice_tools["get_candidate"](candidate=alice_candidate.full_name)
        assert detail["candidate_id"] == str(alice_candidate.id)

    def test_a_partial_name_resolves(self, alice_tools, alice_candidate):
        surname = alice_candidate.full_name.split()[-1]
        detail = alice_tools["get_candidate"](candidate=surname)
        assert detail["candidate_id"] == str(alice_candidate.id)

    def test_an_unmatched_reference_steers_back_to_a_lookup(self, alice_tools):
        """The message matters as much as the failure: the model reported the old
        wording to the recruiter as fact, telling them a candidate it had just been
        reading was not in the repository."""
        with pytest.raises(ToolError, match="list_candidates"):
            alice_tools["get_candidate"](candidate="not-a-uuid")

    def test_an_id_that_belongs_to_nobody_is_not_reported_as_a_missing_person(
        self, alice_tools
    ):
        """Exactly what went wrong in production: a well-formed but invented UUID."""
        with pytest.raises(ToolError) as raised:
            alice_tools["get_candidate"](candidate="302d7759-68e8-4969-81d2-404977057e06")

        message = str(raised.value)
        assert "list_candidates" in message
        assert "probably wrong" in message, "must blame the id, not the candidate"

    def test_an_ambiguous_name_asks_rather_than_guessing(self, session, alice, alice_tools):
        for _ in range(2):
            session.add(Candidate(owner_id=alice.id, full_name="Jordan Avery"))
        session.commit()

        with pytest.raises(ToolError, match="matches 2 candidates"):
            alice_tools["get_candidate"](candidate="Jordan Avery")

    def test_reading_a_candidate_is_audited(self, session, alice_tools, alice_candidate):
        alice_tools["get_candidate"](candidate=str(alice_candidate.id))
        events = session.execute(
            select(AuditEvent).where(
                AuditEvent.resource_id == alice_candidate.id,
                AuditEvent.action == "candidate.viewed",
            )
        ).scalars().all()
        assert events, "assistant reads must leave an audit trail"

    def test_row_cap_is_enforced(self, alice_tools):
        """A tool must not be talked into dumping the whole repository."""
        result = alice_tools["list_candidates"](limit=10_000)
        assert result["returned"] <= 25


class TestWrites:
    def test_create_job_parses_and_audits(self, session, alice, alice_tools):
        result = alice_tools["create_job"](description=JOB, title="Senior Platform Engineer")

        assert result["job_id"]
        assert "extracted_requirements" in result
        # The recruiter is told the extraction is fallible.
        assert "confirm" in result["note"].lower()

        job = job_service.get_job(session, uuid.UUID(result["job_id"]), alice.id)
        assert job is not None

        events = session.execute(
            select(AuditEvent).where(
                AuditEvent.resource_id == job.id, AuditEvent.action == "job.created"
            )
        ).scalars().all()
        assert events, "an assistant write must be audited"
        assert events[0].detail.get("via") == "assistant"

    def test_create_job_rejects_a_stub_description(self, alice_tools):
        with pytest.raises(ToolError, match="40 characters"):
            alice_tools["create_job"](description="Need an engineer.")

    def test_set_recommendation_rejects_an_invalid_value(self, alice_tools):
        with pytest.raises(ToolError, match="must be one of"):
            alice_tools["set_recommendation"](
                screening_id=str(uuid.uuid4()),
                candidate_id=str(uuid.uuid4()),
                recommendation="definitely_hire",
            )


class TestShortlisting:
    """Shortlisting from the assistant.

    Added because asked to "shortlist Sharmila for the AI Engineer role" the
    assistant had no tool that could: it offered set_recommendation, which annotates
    a score and moves nobody, and asked the recruiter for a screening UUID it had no
    way to be given.
    """

    def test_shortlisting_puts_the_candidate_in_the_pipeline(
        self, session, alice, alice_tools, alice_candidate
    ):
        job = job_service.create_job(
            session, owner_id=alice.id, title="Platform Engineer", description=JOB
        )

        result = alice_tools["shortlist_candidate"](
            candidate_id=str(alice_candidate.id), job_id=str(job.id)
        )

        assert result["stage"] == "shortlisted"
        # Named, not identified by uuid — the recruiter has to be able to read it.
        assert result["candidate"] == alice_candidate.full_name
        assert result["job"] == "Platform Engineer"

        application = session.execute(
            select(Application).where(
                Application.candidate_id == alice_candidate.id,
                Application.job_id == job.id,
            )
        ).scalars().first()
        assert application is not None
        assert application.stage == "shortlisted"

    def test_shortlisting_twice_does_not_open_a_second_application(
        self, session, alice, alice_tools, alice_candidate
    ):
        job = job_service.create_job(
            session, owner_id=alice.id, title="Platform Engineer", description=JOB
        )

        first = alice_tools["shortlist_candidate"](
            candidate_id=str(alice_candidate.id), job_id=str(job.id)
        )
        second = alice_tools["shortlist_candidate"](
            candidate_id=str(alice_candidate.id), job_id=str(job.id)
        )

        assert first["application_id"] == second["application_id"]

    def test_an_existing_stage_is_reported_not_reset(
        self, session, alice, alice_tools, alice_candidate
    ):
        """Someone already interviewing must not be dragged back to shortlisted."""
        job = job_service.create_job(
            session, owner_id=alice.id, title="Platform Engineer", description=JOB
        )
        application = application_service.open_application(
            session,
            owner_id=alice.id,
            candidate_id=alice_candidate.id,
            job_id=job.id,
            stage=ApplicationStage.INTERVIEW,
        )

        result = alice_tools["shortlist_candidate"](
            candidate_id=str(alice_candidate.id), job_id=str(job.id)
        )

        assert result["stage"] == "interview"
        assert "left as it is" in result["note"]
        session.refresh(application)
        assert application.stage == "interview"

    def test_another_recruiters_job_is_refused(self, session, bob, alice_tools, alice_candidate):
        theirs = job_service.create_job(
            session, owner_id=bob.id, title="Their role", description=JOB
        )
        with pytest.raises(ToolError):
            alice_tools["shortlist_candidate"](
                candidate_id=str(alice_candidate.id), job_id=str(theirs.id)
            )

    def test_ids_must_be_uuids(self, alice_tools):
        with pytest.raises(ToolError, match="UUIDs"):
            alice_tools["shortlist_candidate"](candidate_id="sharmila", job_id="ai engineer")

    def test_shortlisting_is_marked_as_a_write(self):
        # The transcript marks writes, and an approval step will key off this set.
        from app.agent.tools import WRITE_TOOLS

        assert "shortlist_candidate" in WRITE_TOOLS


class TestIdsAreResolvable:
    def test_list_jobs_exposes_the_latest_screening(self, session, alice, alice_tools):
        """Without this the assistant cannot get from a role name to a screening,
        which is why it resorted to asking the recruiter for a UUID."""
        job = job_service.create_job(
            session, owner_id=alice.id, title="Platform Engineer", description=JOB
        )

        listed = alice_tools["list_jobs"]()
        row = next(entry for entry in listed["jobs"] if entry["job_id"] == str(job.id))

        # Never screened, so there is nothing to point at — and the key still exists
        # so the model does not have to guess whether it was omitted.
        assert row["latest_screening_id"] is None

        screening = matching_service.create_screening(session, job=job)
        matching_service.run_screening(session, screening.id)

        listed = alice_tools["list_jobs"]()
        row = next(entry for entry in listed["jobs"] if entry["job_id"] == str(job.id))
        assert row["latest_screening_id"] == str(screening.id)


class TestFindJobsForCandidate:
    """The counterpart to match_candidates_to_job.

    Without this tool the assistant could only answer "what roles suit this person"
    by listing jobs and comparing titles by eye — which is how a lab technician got
    offered a Fullstack Engineer role on the strength of the word "engineer".
    """

    def test_an_unscreened_candidate_reports_unscored_rather_than_no_fit(
        self, session, alice, alice_tools, alice_candidate
    ):
        """The distinction the tool exists to make.

        "Nobody has measured this" and "no job suits them" are different answers, and
        only the first one is true for a candidate who has never been screened.
        """
        job_service.create_job(
            session, owner_id=alice.id, title="Senior Platform Engineer", description=JOB
        )

        result = alice_tools["find_jobs_for_candidate"](str(alice_candidate.id))

        assert result["returned"] == 0
        assert result["matches"] == []
        assert result["unscored_jobs"] == 1
        # The note is what the model reads; it must not invite a guess from titles.
        assert "not been screened" in result["note"]

    def test_a_screened_candidate_comes_back_with_the_score_and_the_evidence(
        self, session, alice, alice_tools, alice_candidate
    ):
        job = job_service.create_job(
            session, owner_id=alice.id, title="Senior Platform Engineer", description=JOB
        )
        screening = matching_service.create_screening(session, job=job)
        matching_service.run_screening(session, screening.id)

        result = alice_tools["find_jobs_for_candidate"](str(alice_candidate.id))

        assert result["returned"] == 1, result
        match = result["matches"][0]
        assert match["job_title"] == "Senior Platform Engineer"
        assert match["job_id"] == str(job.id)
        assert 0 <= match["score"] <= 100
        assert match["recommendation"]
        # The breakdown travels with the score so the model quotes the gap rather
        # than inferring one from the job title.
        assert match["subscores"], "no subscore breakdown returned"
        assert "matched_skills" in match and "missing_skills" in match
        assert result["unscored_jobs"] == 0

    def test_unscored_jobs_are_counted_not_ranked_low(
        self, session, alice, alice_tools, alice_candidate
    ):
        """A job with no screening must be absent and counted, never a zero — a zero
        would read as "measured and bad"."""
        job = job_service.create_job(
            session, owner_id=alice.id, title="Senior Platform Engineer", description=JOB
        )
        job_service.create_job(
            session, owner_id=alice.id, title="Unrelated Role", description=JOB
        )
        screening = matching_service.create_screening(session, job=job)
        matching_service.run_screening(session, screening.id)

        result = alice_tools["find_jobs_for_candidate"](str(alice_candidate.id))

        assert result["returned"] == 1
        assert result["unscored_jobs"] == 1
        assert [m["job_title"] for m in result["matches"]] == ["Senior Platform Engineer"]

    def test_another_recruiters_candidate_is_not_readable(
        self, session, alice_candidate, bob
    ):
        bob_tools = {tool.__name__: tool for tool in build_toolset(session, bob)}

        with pytest.raises(ToolError):
            bob_tools["find_jobs_for_candidate"](str(alice_candidate.id))

    def test_a_malformed_id_is_a_recoverable_tool_error(self, alice_tools):
        with pytest.raises(ToolError):
            alice_tools["find_jobs_for_candidate"]("the lab technician")

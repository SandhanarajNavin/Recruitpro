"""Tools the recruiter assistant can call.

Two rules govern everything in this module.

**The model never supplies identity.** Every tool is built by ``build_toolset`` as a
closure over the authenticated recruiter and the request's database session. No tool
takes ``recruiter_id`` as a parameter, so no prompt — however crafted — can make the
assistant read another tenant's data. This is the same guarantee the REST endpoints
give, enforced the same way: ownership is a filter in the query, not a check after it.

**The model never produces a score.** It can start a screening and read the results,
but the numbers come from ``services/scoring.py`` exactly as they do for the REST API.
An override is stored alongside the computed score, never replacing it.

Signatures are deliberately flat — primitives and plain defaults — because the Gen AI
SDK builds the function-calling schema by introspecting them, and the docstrings
become the tool descriptions the model reads.
"""

# NOTE: deliberately no `from __future__ import annotations` in this module.
# PEP 563 turns every annotation into a string, and the Gen AI SDK builds the
# function-calling schema by reading `inspect.signature()` without resolving them.
# With it enabled, every tool that takes a parameter is advertised with a broken
# schema and silently fails at call time — only zero-argument tools work.

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import (
    ApplicationStage,
    Candidate,
    CandidateProfile,
    Job,
    Screening,
    ScreeningResult,
    User,
)
from app.ai.retrieval import passage_search
from app.db.models.screening import ScreeningStatus
from app.services import (
    analytics_service,
    application_service,
    audit_service,
    candidate_service,
    job_service,
    matching_service,
    search_service,
)
from app.services.application_service import ApplicationError
from app.services.scoring import CATEGORY_LABELS

logger = get_logger(__name__)

#: Cap on rows returned to the model. A tool that dumps the whole repository into the
#: context window is slow, expensive, and pushes the actual question out of scope.
MAX_ROWS = 25


class ToolError(RuntimeError):
    """Recoverable — the message goes back to the model so it can correct itself."""


def _resolve_candidate(session: Session, owner_id: uuid.UUID, reference: str) -> Candidate:
    """A candidate from either a UUID or a name, for read-only tools.

    Models do not reproduce UUIDs reliably. Observed in a real transcript: the
    assistant looked Kevin Thompson up, described him correctly, then on the next two
    turns passed ids that belong to nobody — neither matched the one the lookup had
    returned. Accepting the name removes the need to carry a 36-character random
    string across turns at all.

    Every failure here is phrased as "this lookup did not resolve", never as "no such
    person". The model reported the old wording to the recruiter as fact and told them
    a candidate it had just been reading was not in the repository.
    """
    text_reference = (reference or "").strip()
    if not text_reference:
        raise ToolError("Give a candidate name or id to look up.")

    try:
        candidate_id = uuid.UUID(text_reference)
    except ValueError:
        candidate_id = None

    if candidate_id is not None:
        candidate = session.execute(
            select(Candidate).where(
                Candidate.id == candidate_id, Candidate.owner_id == owner_id
            )
        ).scalars().first()
        if candidate is not None:
            return candidate
        # An id that resolves to nothing is far more likely to be misremembered than
        # to mean the candidate was deleted, so send the model back to a name lookup
        # rather than letting it conclude the person is gone.
        raise ToolError(
            f"No candidate has the id {text_reference}. Do not tell the recruiter the "
            "person is missing — that id is probably wrong. Call list_candidates with "
            "their name to get the correct id, then try again."
        )

    rows, _total = candidate_service.list_candidates(
        session, owner_id, query=text_reference, limit=MAX_ROWS, offset=0
    )
    if not rows:
        raise ToolError(
            f"Nothing in this repository matches {text_reference!r}. Try list_candidates "
            "with a shorter or differently spelled version of the name before telling "
            "the recruiter there is no such candidate."
        )
    if len(rows) > 1:
        names = ", ".join(f"{row.full_name} ({row.id})" for row in rows[:5])
        raise ToolError(
            f"{text_reference!r} matches {len(rows)} candidates: {names}. "
            "Ask the recruiter which one, or call again with the id."
        )
    return rows[0]


def _profile_for(session: Session, candidate_id: uuid.UUID) -> CandidateProfile | None:
    return session.execute(
        select(CandidateProfile)
        .where(CandidateProfile.candidate_id == candidate_id)
        .order_by(CandidateProfile.version.desc())
        .limit(1)
    ).scalars().first()


def build_toolset(session: Session, user: User) -> list[Callable[..., Any]]:
    """Return the tool functions bound to this recruiter and this session."""

    owner_id = user.id

    # ── read ─────────────────────────────────────────────────────────────

    def list_candidates(
        skill: str = "", min_years: int = 0, query: str = "", limit: int = 10
    ) -> dict:
        """List candidates in this recruiter's repository.

        Args:
            skill: Filter to candidates who have this skill, e.g. "Kubernetes".
                Matched as a whole skill, case-insensitively — a role phrase like
                "fullstack developer" belongs in `query`, not here.
            min_years: Minimum total years of professional experience.
            query: Free-text match against name, email, current title, primary role
                and secondary roles. Use this for a role or job title. Spelling and
                spacing do not have to match: "fulstak", "full-stack" and "Full Stack"
                all reach a Full Stack Developer, so pass the recruiter's own wording
                rather than correcting it or giving up after one empty result.
            limit: Maximum candidates to return, at most 25.
        """
        rows, total = candidate_service.list_candidates(
            session,
            owner_id,
            query=query or None,
            skill=skill or None,
            min_years=min_years or None,
            limit=min(limit, MAX_ROWS),
            offset=0,
        )
        out = []
        for candidate in rows:
            profile = candidate.latest_profile
            out.append(
                {
                    "candidate_id": str(candidate.id),
                    "name": candidate.full_name,
                    "current_title": profile.current_title if profile else None,
                    "years": float(profile.total_years_experience or 0) if profile else 0.0,
                    # Truncated: the model needs enough to reason, not the full list.
                    "top_skills": list(profile.skills or [])[:12] if profile else [],
                    "primary_role": profile.primary_role if profile else None,
                }
            )
        return {"total_matching": total, "returned": len(out), "candidates": out}

    def get_candidate(candidate: str) -> dict:
        """Full profile for one candidate: skills, experience, education, role.

        Args:
            candidate: The candidate's name or their UUID. A name is fine and is
                safer than recalling an id from earlier in the conversation — pass
                "Kevin Thompson" rather than a UUID you are reconstructing from
                memory. If an id does not resolve, look the name up with
                list_candidates rather than reporting the candidate as missing.
        """
        found = _resolve_candidate(session, owner_id, candidate)
        cid = found.id
        profile = _profile_for(session, cid)
        audit_service.record(
            session, "candidate.viewed", recruiter_id=owner_id,
            resource_type="candidate", resource_id=cid, via="assistant",
        )
        return {
            "candidate_id": str(found.id),
            "name": found.full_name,
            "email": found.email,
            "location": found.location,
            "summary": found.summary,
            "current_title": profile.current_title if profile else None,
            "years": float(profile.total_years_experience or 0) if profile else 0.0,
            "primary_role": profile.primary_role if profile else None,
            "secondary_roles": list(profile.secondary_roles or []) if profile else [],
            "skills": list(profile.skills or []) if profile else [],
            "industries": list(profile.domains or []) if profile else [],
            "education": list(profile.education or []) if profile else [],
            "certifications": list(profile.certifications or []) if profile else [],
            "experience": list(profile.experience or []) if profile else [],
            "parsed_by": profile.parser_model if profile else None,
        }

    def find_jobs_for_candidate(candidate: str, limit: int = 5) -> dict:
        """Which of this recruiter's jobs a candidate scores best against.

        The counterpart to match_candidates_to_job, which goes the other way. Use this
        for "what roles suit this person" — do not answer that by listing jobs and
        comparing titles by eye, which mistakes a job whose title happens to share a
        word for a job the candidate can actually do.

        Only jobs this candidate has already been screened against have a score, and
        `unscored_jobs` counts the ones that do not. A candidate nobody has screened
        returns no matches and every job unscored: that means "not measured yet", not
        "no good fit". Say so, and offer match_candidates_to_job for a specific role
        rather than implying the list is complete.

        Args:
            candidate: The candidate's name or their UUID. A name is fine and is
                safer than recalling an id from earlier in the conversation.
            limit: Maximum jobs to return, at most 25.
        """
        found = _resolve_candidate(session, owner_id, candidate)

        matches, unscored = candidate_service.top_job_matches(
            session, found.id, owner_id, limit=min(max(limit, 1), MAX_ROWS)
        )
        return {
            "candidate": found.full_name,
            "returned": len(matches),
            "unscored_jobs": unscored,
            "matches": [
                {
                    "job_id": str(row["job_id"]),
                    "job_title": row["job_title"],
                    "job_status": row["job_status"],
                    "score": row["score"],
                    "recommendation": row["recommendation"],
                    "screening_id": str(row["screening_id"]),
                    # The evidence behind the number, so the model quotes the gap
                    # rather than inferring one from the title.
                    "matched_skills": row["matched_skills"],
                    "missing_skills": row["missing_skills"],
                    "subscores": [
                        {"category": entry["label"], "score": entry["score"]}
                        for entry in row["subscores"]
                    ],
                }
                for row in matches
            ],
            "note": (
                f"{found.full_name} has not been screened against any job yet, so "
                f"there is no fit score for any of the {unscored} job(s) on the board. "
                "Do not guess from job titles — offer to run a screening."
                if not matches
                else (
                    f"Scored against {len(matches)} job(s); {unscored} other job(s) "
                    "have never been screened against this candidate and are absent "
                    "from this list rather than ranked low."
                )
            ),
        }

    def list_jobs(limit: int = 10) -> dict:
        """List job descriptions this recruiter has created.

        Each job carries its most recent screening id, so a request phrased by role
        name ("the AI Engineer role") can be resolved to the ids the other tools
        need without asking the recruiter for a UUID.

        Args:
            limit: Maximum jobs to return, at most 25.
        """
        jobs = job_service.list_jobs(session, owner_id, limit=min(limit, MAX_ROWS))

        # Latest completed screening per job, in one query rather than per row.
        latest_screening: dict[uuid.UUID, str] = {}
        if jobs:
            for job_id, screening_id in session.execute(
                select(Screening.job_id, Screening.id)
                .where(
                    Screening.job_id.in_([job.id for job in jobs]),
                    Screening.status == ScreeningStatus.COMPLETED.value,
                )
                .order_by(Screening.job_id, Screening.finished_at.desc())
            ).all():
                latest_screening.setdefault(job_id, str(screening_id))

        return {
            "jobs": [
                {
                    "job_id": str(job.id),
                    "title": job.title,
                    "location": job.location,
                    "status": job.status,
                    "created": job.created_at.date().isoformat(),
                    "required_skills": [
                        entry.get("skill")
                        for entry in (job.requirement.required_skills if job.requirement else [])
                    ],
                    "min_years": job.requirement.min_years_experience if job.requirement else None,
                    # None means the job has never been screened, so there are no
                    # results to read and no screening to record a decision against.
                    "latest_screening_id": latest_screening.get(job.id),
                }
                for job in jobs
            ]
        }

    def repository_stats() -> dict:
        """Headline counts for this recruiter: candidates, resumes, jobs, shortlisted."""
        return {
            metric.label: {"value": metric.value, "change_pct_30d": metric.delta_pct}
            for metric in analytics_service.headline_metrics(session, owner_id)
        } | {
            "candidates_by_role": {
                slice_.role: slice_.count
                for slice_ in analytics_service.candidates_by_role(session, owner_id)
            }
        }

    def get_screening_results(screening_id: str) -> dict:
        """Ranked results of a completed screening, with evidence per candidate.

        Args:
            screening_id: The screening's UUID.
        """
        try:
            sid = uuid.UUID(screening_id)
        except ValueError as exc:
            raise ToolError(f"{screening_id!r} is not a valid screening id.") from exc

        screening = session.execute(
            select(Screening).join(Job, Job.id == Screening.job_id)
            .where(Screening.id == sid, Job.owner_id == owner_id)
        ).scalars().first()
        if screening is None:
            raise ToolError("No such screening in this account.")

        rows = session.execute(
            select(ScreeningResult)
            .where(ScreeningResult.screening_id == sid)
            .order_by(ScreeningResult.rank)
            .limit(MAX_ROWS)
        ).scalars().all()

        return {
            "screening_id": str(sid),
            "status": screening.status,
            "engine": screening.mode,
            "degradations": list(screening.degradations or []),
            "results": [
                {
                    "rank": row.rank,
                    "candidate_id": str(row.candidate_id),
                    "score": float(row.composite_score),
                    "recommendation": row.recommendation,
                    "shortlisted": row.shortlisted,
                    "matched_skills": list(row.matched_skills or []),
                    "missing_skills": list(row.missing_skills or []),
                    "subscores": {
                        CATEGORY_LABELS.get(key, key): value.get("score")
                        for key, value in (row.subscores or {}).items()
                    },
                }
                for row in rows
            ],
        }

    # ── search ───────────────────────────────────────────────────────────

    def match_candidates_to_job(job_id: str, page_size: int = 5) -> dict:
        """Run the matching pipeline for a job and return the top candidates.

        This is the expensive tool: it retrieves, reranks, evaluates each survivor
        with the model, and scores them. Expect it to take up to a minute. Prefer
        get_screening_results if a screening for this job already exists.

        Args:
            job_id: The job's UUID, as returned by list_jobs.
            page_size: How many candidates to return in the first page.
        """
        try:
            jid = uuid.UUID(job_id)
        except ValueError as exc:
            raise ToolError(f"{job_id!r} is not a valid job id.") from exc

        job = job_service.get_job(session, jid, owner_id)
        if job is None:
            raise ToolError("No such job in this account.")
        if job.requirement is None:
            raise ToolError("That job has no parsed requirements; it needs re-creating.")

        screening = matching_service.create_screening(session, job=job)
        matching_service.run_screening(session, screening.id)
        session.refresh(screening)

        search = search_service.create_session(
            session, recruiter_id=owner_id, screening=screening, page_size=page_size
        )
        page = search_service.next_page(session, search)
        return {
            "screening_id": str(screening.id),
            "search_session_id": str(search.id),
            "engine": screening.mode,
            "total_ranked": page.total,
            "remaining": page.remaining,
            "candidates": [
                {
                    "rank": row.rank,
                    "candidate_id": str(row.candidate_id),
                    "name": page.candidates[row.candidate_id].full_name
                    if row.candidate_id in page.candidates
                    else "Unknown",
                    "score": float(row.score),
                }
                for row in page.results
            ],
        }

    def show_more_candidates(search_session_id: str) -> dict:
        """Next page of an existing search, excluding everyone already shown.

        Args:
            search_session_id: Returned by match_candidates_to_job.
        """
        try:
            sid = uuid.UUID(search_session_id)
        except ValueError as exc:
            raise ToolError(f"{search_session_id!r} is not a valid session id.") from exc

        try:
            search = search_service.get_session(session, sid, owner_id)
        except search_service.SearchSessionNotFound as exc:
            raise ToolError("No such search session in this account.") from exc

        page = search_service.next_page(session, search)
        return {
            "remaining": page.remaining,
            "exhausted": page.remaining == 0 and not page.results,
            "candidates": [
                {
                    "rank": row.rank,
                    "candidate_id": str(row.candidate_id),
                    "name": page.candidates[row.candidate_id].full_name
                    if row.candidate_id in page.candidates
                    else "Unknown",
                    "score": float(row.score),
                }
                for row in page.results
            ],
        }

    # ── write ────────────────────────────────────────────────────────────

    def create_job(description: str, title: str = "", location: str = "") -> dict:
        """Create a job description and parse its requirements.

        Only call this when the recruiter has clearly asked to create a role. Echo
        the extracted requirements back to them afterwards so they can correct
        anything that was read wrongly.

        Args:
            description: The full job description text, at least 40 characters.
            title: Optional title; extracted from the description when omitted.
            location: Optional location.
        """
        if len(description.strip()) < 40:
            raise ToolError("A job description needs at least 40 characters of detail.")

        job = job_service.create_job(
            session,
            owner_id=owner_id,
            title=title or None,
            description=description,
            location=location or None,
        )
        audit_service.record(
            session, "job.created", recruiter_id=owner_id,
            resource_type="job", resource_id=job.id, via="assistant",
        )
        requirement = job.requirement
        required = requirement.required_skills if requirement else []
        return {
            "job_id": str(job.id),
            "title": job.title,
            "extracted_requirements": {
                "seniority": requirement.seniority if requirement else None,
                "min_years": requirement.min_years_experience if requirement else None,
                "required_skills": [entry.get("skill") for entry in required],
                "preferred_skills": list(requirement.preferred_skills or []) if requirement else [],
                "industries": list(requirement.domains or []) if requirement else [],
            },
            "note": "Requirements were extracted automatically. Ask the recruiter to confirm them.",
        }

    def set_recommendation(
        screening_id: str, candidate_id: str, recommendation: str, note: str = ""
    ) -> dict:
        """Record the recruiter's own recommendation for a candidate on a screening.

        This does not change the computed score — it is stored alongside it, and the
        original ranking stays intact and auditable.

        Args:
            screening_id: The screening's UUID.
            candidate_id: The candidate's UUID.
            recommendation: One of strong_hire, interview, maybe, pass.
            note: Optional reason, shown next to the override in the UI.
        """
        allowed = {"strong_hire", "interview", "maybe", "pass"}
        if recommendation not in allowed:
            raise ToolError(f"recommendation must be one of {sorted(allowed)}.")

        try:
            sid, cid = uuid.UUID(screening_id), uuid.UUID(candidate_id)
        except ValueError as exc:
            raise ToolError("screening_id and candidate_id must both be UUIDs.") from exc

        result = session.execute(
            select(ScreeningResult)
            .join(Screening, Screening.id == ScreeningResult.screening_id)
            .join(Job, Job.id == Screening.job_id)
            .where(
                ScreeningResult.screening_id == sid,
                ScreeningResult.candidate_id == cid,
                Job.owner_id == owner_id,
            )
        ).scalars().first()
        if result is None:
            raise ToolError("No such candidate on that screening in this account.")

        result.override_recommendation = recommendation
        result.override_note = note or None
        result.override_by = owner_id
        session.commit()

        audit_service.record(
            session, "screening.override", recruiter_id=owner_id,
            resource_type="screening_result", resource_id=result.id,
            recommendation=recommendation, via="assistant",
        )
        return {
            "candidate_id": candidate_id,
            "computed_score": float(result.composite_score),
            "computed_recommendation": result.recommendation,
            "your_recommendation": recommendation,
            "note": "The computed score is unchanged; your recommendation is stored alongside it.",
        }

    def search_resume_text(query: str, candidate_id: str = "", limit: int = 5) -> dict:
        """Search what resumes actually say, and quote the passages back.

        The other tools read parsed fields — skills, titles, years. This one searches
        the resume text itself, so it answers questions those fields cannot: what
        someone built at a particular company, whether anyone has done a specific
        kind of work, the wording of an achievement.

        Quote the returned passages when they answer the question, and attribute each
        to the candidate it came from. A passage is evidence; do not paraphrase it
        into a claim the text does not make.

        Across the repository this returns each matching candidate's single best
        passage, so the list is people rather than paragraphs. Use
        `matched_candidates` for "how many", never the length of the passage list —
        that is capped by `limit`. When `more_beyond_window` is true the count is a
        lower bound, so report it as "at least N". Scoped to one `candidate_id` it
        returns several passages from that one resume instead.

        Args:
            query: What to look for, in words. "Kubernetes migration", "led a team".
            candidate_id: Optional. Restrict to one candidate's resume.
            limit: Maximum passages to return, at most 10.
        """
        target: uuid.UUID | None = None
        if candidate_id:
            try:
                target = uuid.UUID(candidate_id)
            except ValueError as exc:
                raise ToolError(f"{candidate_id!r} is not a valid candidate id.") from exc

        found = passage_search.search_passages(
            session,
            owner_id=owner_id,
            query=query,
            candidate_id=target,
            limit=min(max(limit, 1), 10),
        )
        passages = found.passages

        if not passages:
            # Said plainly so the model reports an empty result rather than filling
            # the gap from the parsed profile and presenting it as a quote.
            note = (
                "These are verbatim resume passages. Nothing matched; say so rather "
                "than answering from the parsed profile."
            )
        elif target is not None:
            note = "Verbatim passages from this candidate's resume, ranked by similarity."
        else:
            # matched_candidates is what makes "how many people" answerable: the
            # passage list is capped, so its length is a page size and not a total.
            # Stated as a floor when the search window filled up, because then the
            # count is the number found, not the number that exist.
            note = (
                "These are verbatim resume passages, one per candidate, ranked by "
                f"similarity. {found.matched_candidates} candidate(s) matched"
                + (
                    " within the search window — there may be more, so say 'at least' "
                    "rather than giving this as a total."
                    if found.truncated
                    else " in the whole repository."
                )
            )

        return {
            "returned": len(passages),
            #: Distinct candidates that matched, which is not len(passages) — the
            #: list is capped at `limit`.
            "matched_candidates": found.matched_candidates,
            "more_beyond_window": found.truncated,
            "passages": [
                {
                    "candidate_id": str(item.candidate_id),
                    "candidate": item.candidate_name,
                    "text": item.text,
                    "similarity": item.similarity,
                }
                for item in passages
            ],
            "note": note,
        }

    def shortlist_candidate(candidate_id: str, job_id: str, note: str = "") -> dict:
        """Put a candidate into a job's hiring pipeline at the shortlisted stage.

        This is what "shortlist X for Y" means — the same action as the Shortlist
        button on a screening. It is distinct from ``set_recommendation``, which
        annotates a score without moving anyone: recording "interview" on a
        screening result does not put a candidate in the pipeline.

        Idempotent. If they are already in this job's pipeline, their current stage
        is returned unchanged rather than being reset to shortlisted.

        Args:
            candidate_id: The candidate's UUID, from list_candidates.
            job_id: The job's UUID, from list_jobs.
            note: Optional reason, recorded on the pipeline event.
        """
        try:
            cid, jid = uuid.UUID(candidate_id), uuid.UUID(job_id)
        except ValueError as exc:
            raise ToolError("candidate_id and job_id must both be UUIDs.") from exc

        # Their best result for this job, so the pipeline entry records the score
        # they came in on — the same provenance the UI's button gives it.
        origin = session.execute(
            select(ScreeningResult.id)
            .join(Screening, Screening.id == ScreeningResult.screening_id)
            .where(
                ScreeningResult.candidate_id == cid,
                Screening.job_id == jid,
            )
            .order_by(ScreeningResult.composite_score.desc())
            .limit(1)
        ).scalar()

        try:
            application = application_service.open_application(
                session,
                owner_id=owner_id,
                candidate_id=cid,
                job_id=jid,
                stage=ApplicationStage.SHORTLISTED,
                screening_result_id=origin,
                actor_id=owner_id,
            )
        except ApplicationError as exc:
            raise ToolError(str(exc)) from exc

        candidate = session.get(Candidate, cid)
        job = session.get(Job, jid)
        already = application.stage != ApplicationStage.SHORTLISTED.value
        return {
            "application_id": str(application.id),
            "candidate": candidate.full_name if candidate else candidate_id,
            "job": job.title if job else job_id,
            "stage": application.stage,
            "note": (
                f"Already in this pipeline at stage '{application.stage}'; left as it is."
                if already
                else "Shortlisted."
            ),
            "scored_on_entry": float(application.match_score_at_entry)
            if application.match_score_at_entry is not None
            else None,
        }

    return [
        list_candidates,
        get_candidate,
        find_jobs_for_candidate,
        list_jobs,
        repository_stats,
        get_screening_results,
        search_resume_text,
        match_candidates_to_job,
        show_more_candidates,
        create_job,
        shortlist_candidate,
        set_recommendation,
    ]


#: Tools that change stored data. Surfaced so the UI can mark them in the transcript
#: and so an approval step can be added later without hunting through this module.
WRITE_TOOLS = frozenset({"create_job", "shortlist_candidate", "set_recommendation"})

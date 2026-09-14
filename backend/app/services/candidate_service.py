"""Candidate repository reads (architecture doc §8).

Search is a plain SQL concern here. Semantic search over the repository is a
screening operation, not a browse operation — a recruiter listing candidates wants
deterministic filters, not nearest-neighbour ordering.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from difflib import SequenceMatcher

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session, aliased, selectinload

from app.db.models import (
    Candidate,
    CandidateProfile,
    CandidateSkill,
    CandidateStatus,
    Job,
    Resume,
    ResumeStatus,
    Screening,
    ScreeningResult,
    Skill,
)
from app.services.scoring import subscore_rows

#: Titles and role labels are written inconsistently — "Full Stack Developer",
#: "Full-Stack Developer" and "fullstack developer" are one role. Comparing with the
#: separators stripped from both sides makes a recruiter's phrasing match the
#: parser's without a synonym table.
_SEPARATORS = re.compile(r"[^a-z0-9]+")


def _squash_text(value: str) -> str:
    """Lowercased with everything but letters and digits removed."""
    return _SEPARATORS.sub("", value.lower())


def _squash_column(column):
    """The SQL equivalent of :func:`_squash_text`, for use in a comparison."""
    return func.regexp_replace(func.lower(column), "[^a-z0-9]+", "", "g")


def _tokens(value: str) -> list[str]:
    return [token for token in _SEPARATORS.split(value.lower()) if token]


#: How close a typed word must be to a stored one to count as the same word.
#: 0.8 was chosen against the seeded titles: it accepts "fulstak"/"Full Stack",
#: "devlopr"/"Developer" and "machin lerning"/"Machine Learning" while still
#: rejecting an unrelated word like "plumber".
_FUZZY_THRESHOLD = 0.8


def _role_variants(value: str) -> set[str]:
    """Each word of ``value`` plus every run of adjacent words, joined.

    The runs are what let a single typed word reach a multi-word title: "fulstak"
    has to be compared against "fullstack", not against "full" and "stack"
    separately, or the typo spans the word boundary and nothing matches.
    """
    tokens = _tokens(value)
    variants = set(tokens)
    for width in (2, 3):
        for start in range(len(tokens) - width + 1):
            variants.add("".join(tokens[start : start + width]))
    return variants


def _fuzzy_matches(query: str, targets: Iterable[str]) -> bool:
    """True when every word of ``query`` closely matches something in ``targets``.

    Every word must match — "devops engineer" should not be satisfied by a
    candidate who merely matches "engineer".
    """
    words = _tokens(query)
    if not words:
        return False
    pool: set[str] = set()
    for target in targets:
        if target:
            pool |= _role_variants(target)
    if not pool:
        return False
    return all(
        max(SequenceMatcher(None, word, option).ratio() for option in pool)
        >= _FUZZY_THRESHOLD
        for word in words
    )


def _fuzzy_candidate_ids(
    session: Session, owner_id: uuid.UUID, status: str, query: str
) -> list[uuid.UUID]:
    """Candidate ids whose name or latest role labels are a near match for ``query``.

    Scored in Python rather than SQL: pg_trgm is not installed, and on these titles
    its similarity scores do not separate cleanly enough to threshold — "devops"
    scores higher against "Full Stack Developer" than "fulstak" does. Only reached
    when the exact search found nothing, and only ever over one recruiter's own
    candidates, so the scan is bounded.
    """
    latest = (
        select(
            CandidateProfile.current_title,
            CandidateProfile.primary_role,
            CandidateProfile.secondary_roles,
        )
        .where(CandidateProfile.candidate_id == Candidate.id)
        .order_by(CandidateProfile.version.desc())
        .limit(1)
        .subquery()
        .lateral("latest_role")
    )
    rows = session.execute(
        select(
            Candidate.id,
            Candidate.full_name,
            latest.c.current_title,
            latest.c.primary_role,
            latest.c.secondary_roles,
        )
        .outerjoin(latest, onclause=text("true"))
        .where(Candidate.owner_id == owner_id, Candidate.status == status)
    ).all()

    return [
        row.id
        for row in rows
        if _fuzzy_matches(
            query,
            [row.full_name, row.current_title, row.primary_role, *(row.secondary_roles or [])],
        )
    ]


def list_candidates(
    session: Session,
    owner_id: uuid.UUID,
    *,
    query: str | None = None,
    skill: str | None = None,
    location: str | None = None,
    min_years: float | None = None,
    status: str = CandidateStatus.ACTIVE.value,
    limit: int = 50,
    offset: int = 0,
    fuzzy: bool = True,
) -> tuple[list[Candidate], int]:
    """Returns a page of candidates and the total matching count.

    A free-text ``query`` is matched exactly first. Only if that finds nothing is it
    retried as a fuzzy match, so a misspelling like "fulstak" still reaches a Full
    Stack Developer without a well-spelled search ever being widened.

    ``fuzzy=False`` disables that retry, for a caller that needs exactly what was
    asked for — and so the exact path can be tested on its own, since otherwise the
    retry quietly returns the right rows even when the exact match is broken.
    """

    def base_statement():
        return (
            select(Candidate)
            .where(Candidate.owner_id == owner_id, Candidate.status == status)
            .options(
                selectinload(Candidate.profiles),
                selectinload(Candidate.resumes),
            )
        )

    def text_filter(query: str):
        """The exact-match clause for a free-text query: name, email, title, roles."""
        pattern = f"%{query.strip()}%"
        # A recruiter searching "fullstack developer" means the job, not a person's
        # name, so the free-text surface covers the latest profile's title and roles
        # as well. An EXISTS rather than a join, so a candidate whose resume has not
        # been parsed into a profile yet is still findable by name or email.
        squashed = _squash_text(query)
        if squashed:
            any_version = aliased(CandidateProfile)
            # .correlate() is required: otherwise the aggregate subquery puts its own
            # copy of `candidates` in the FROM clause and takes the max version over
            # the whole table instead of over this candidate's profiles.
            latest_version = (
                select(func.max(any_version.version))
                .where(any_version.candidate_id == Candidate.id)
                .correlate(Candidate)
                .scalar_subquery()
            )
            role_elements = func.jsonb_array_elements_text(
                CandidateProfile.secondary_roles
            ).table_valued("value", name="secondary_role")
            role_pattern = f"%{squashed}%"
            title_matches = (
                select(1)
                .select_from(CandidateProfile)
                .where(
                    CandidateProfile.candidate_id == Candidate.id,
                    CandidateProfile.version == latest_version,
                    or_(
                        _squash_column(CandidateProfile.current_title).like(role_pattern),
                        _squash_column(CandidateProfile.primary_role).like(role_pattern),
                        select(1)
                        .select_from(role_elements)
                        .where(_squash_column(role_elements.c.value).like(role_pattern))
                        .exists(),
                    ),
                )
                .exists()
            )
            title_clauses = [title_matches]
        else:
            title_clauses = []

        return or_(
            Candidate.full_name.ilike(pattern),
            Candidate.email.ilike(pattern),
            *title_clauses,
        )

    def apply_attribute_filters(statement):
        if location:
            # On `candidates`, not the profile, so this needs no join and works for a
            # candidate whose resume has not been parsed yet. Substring rather than
            # equality because the column holds whatever the resume wrote —
            # "Chennai, India" and "Chennai" are the same place to a recruiter.
            statement = statement.where(
                Candidate.location.ilike(f"%{location.strip()}%")
            )

        if skill or min_years is not None:
            # Lateral join to the latest profile version only, so a candidate is never
            # matched on a skill that appears solely in an older profile.
            latest = (
                select(
                    CandidateProfile.skills.label("skills"),
                    CandidateProfile.total_years_experience.label("years"),
                )
                .where(CandidateProfile.candidate_id == Candidate.id)
                .order_by(CandidateProfile.version.desc())
                .limit(1)
                .subquery()
                .lateral("latest_profile")
            )
            statement = statement.join(latest, onclause=text("true"))

            if skill:
                wanted = skill.strip().lower()

                # Expand the JSONB array and compare case-insensitively — "postgresql"
                # and "PostgreSQL" are the same skill.
                elements = func.jsonb_array_elements_text(latest.c.skills).table_valued(
                    "value", name="owned_skill"
                )
                literal_match = (
                    select(1)
                    .select_from(elements)
                    .where(func.lower(elements.c.value) == wanted)
                    .exists()
                )

                # The profile stores whatever the parser called it, and the parser
                # calls the same technology different things on different resumes:
                # "React" on one and "React.js" on the next. Matching the raw array
                # alone meant a recruiter searching React found six candidates in one
                # repository and none in another, purely by resume wording.
                #
                # skill_normalizer already resolves both onto one canonical row —
                # React carries "react.js" as an alias — so the canonical edge is
                # checked as well. Kept as an OR rather than a replacement: a profile
                # parsed before the taxonomy existed has no canonical edge yet, and
                # must stay findable.
                alias_values = func.jsonb_array_elements_text(Skill.aliases).table_valued(
                    "value", name="skill_alias"
                )
                canonical_match = (
                    select(1)
                    .select_from(CandidateSkill)
                    .join(Skill, Skill.id == CandidateSkill.skill_id)
                    .where(
                        CandidateSkill.candidate_id == Candidate.id,
                        or_(
                            func.lower(Skill.canonical_name) == wanted,
                            select(1)
                            .select_from(alias_values)
                            .where(func.lower(alias_values.c.value) == wanted)
                            .exists(),
                        ),
                    )
                    .exists()
                )

                statement = statement.where(or_(literal_match, canonical_match))

            if min_years is not None:
                statement = statement.where(latest.c.years >= min_years)

        return statement

    def build(text_clause):
        statement = base_statement()
        if text_clause is not None:
            statement = statement.where(text_clause)
        return apply_attribute_filters(statement)

    def count(statement) -> int:
        return int(
            session.execute(
                select(func.count()).select_from(statement.order_by(None).subquery())
            ).scalar()
            or 0
        )

    statement = build(text_filter(query) if query else None)
    total = count(statement)

    if query and not total and fuzzy:
        near = _fuzzy_candidate_ids(session, owner_id, status, query)
        if near:
            statement = build(Candidate.id.in_(near))
            total = count(statement)

    rows = list(
        session.execute(
            statement.order_by(Candidate.created_at.desc()).limit(limit).offset(offset)
        )
        .scalars()
        .unique()
        .all()
    )
    return rows, total


def get_candidate(
    session: Session, candidate_id: uuid.UUID, owner_id: uuid.UUID
) -> Candidate | None:
    return session.execute(
        select(Candidate)
        .where(Candidate.id == candidate_id, Candidate.owner_id == owner_id)
        .options(selectinload(Candidate.profiles), selectinload(Candidate.resumes))
    ).scalars().first()


def repository_stats(session: Session, owner_id: uuid.UUID) -> dict[str, int]:
    """Dashboard counters."""
    total = session.execute(
        select(func.count(Candidate.id)).where(
            Candidate.owner_id == owner_id, Candidate.status == CandidateStatus.ACTIVE.value
        )
    ).scalar() or 0

    by_status = dict(
        session.execute(
            select(Resume.status, func.count(Resume.id))
            .join(Candidate)
            .where(Candidate.owner_id == owner_id)
            .group_by(Resume.status)
        ).all()
    )

    return {
        "candidates": int(total),
        "resumes_ready": int(by_status.get(ResumeStatus.READY.value, 0)),
        "resumes_processing": int(
            sum(
                by_status.get(status.value, 0)
                for status in (
                    ResumeStatus.QUEUED,
                    ResumeStatus.EXTRACTING,
                    ResumeStatus.PARSING,
                    ResumeStatus.EMBEDDING,
                )
            )
        ),
        "resumes_failed": int(by_status.get(ResumeStatus.FAILED.value, 0)),
    }


def pool_summary(session: Session, owner_id: uuid.UUID) -> dict[str, int]:
    """Counts for the tiles above the candidate list.

    Pool-level, not page-level: a tile that changed when you turned the page would
    be describing the page rather than the pool.

    "To review" is candidates nobody has acted on yet — no application opened. That
    is the actionable number; "not yet screened" would count people the recruiter
    has already decided about.
    """
    from app.db.models import Application, Job

    active = (Candidate.owner_id == owner_id, Candidate.status == CandidateStatus.ACTIVE.value)

    total = session.execute(
        select(func.count(Candidate.id)).where(*active)
    ).scalar() or 0

    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    new_today = session.execute(
        select(func.count(Candidate.id)).where(*active, Candidate.created_at >= midnight)
    ).scalar() or 0

    with_application = (
        select(Application.candidate_id)
        .join(Job, Job.id == Application.job_id)
        .where(Job.owner_id == owner_id)
        .distinct()
        .scalar_subquery()
    )
    to_review = session.execute(
        select(func.count(Candidate.id)).where(
            *active, Candidate.id.notin_(with_application)
        )
    ).scalar() or 0

    shortlisted = session.execute(
        select(func.count(func.distinct(Application.candidate_id)))
        .join(Job, Job.id == Application.job_id)
        .where(
            Job.owner_id == owner_id,
            Application.stage.in_(
                ["shortlisted", "interview", "offer", "hired"]
            ),
        )
    ).scalar() or 0

    return {
        "total": int(total),
        "new_today": int(new_today),
        "to_review": int(to_review),
        "shortlisted": int(shortlisted),
    }


#: Fields a recruiter may edit by hand. Parser output is deliberately absent: a
#: reprocess rewrites it, so an edit there would silently disappear.
CANDIDATE_EDITABLE = ("summary", "notes", "location", "email", "phone")


class CandidateError(ValueError):
    """A candidate update the domain refuses."""


def update_candidate(
    session: Session,
    candidate_id: uuid.UUID,
    owner_id: uuid.UUID,
    *,
    status: str | None = None,
    **fields: str | None,
) -> Candidate:
    """Apply a recruiter's edit to a candidate they own.

    Ownership is a query filter, so another recruiter's candidate is
    indistinguishable from one that does not exist.
    """
    candidate = session.execute(
        select(Candidate).where(Candidate.id == candidate_id, Candidate.owner_id == owner_id)
    ).scalar_one_or_none()
    if candidate is None:
        raise CandidateError("Candidate not found.")

    if status is not None:
        valid = {member.value for member in CandidateStatus}
        if status not in valid:
            raise CandidateError(f"Unknown status {status!r}. Expected one of {sorted(valid)}.")
        candidate.status = status

    for name, value in fields.items():
        if name not in CANDIDATE_EDITABLE:
            raise CandidateError(f"{name} cannot be edited.")
        if value is not None:
            # Empty string clears the field; None means "leave alone".
            setattr(candidate, name, value.strip() or None)

    session.commit()
    session.refresh(candidate)
    return candidate


def top_job_matches(
    session: Session, candidate_id: uuid.UUID, owner_id: uuid.UUID, *, limit: int = 5
) -> tuple[list[dict], int]:
    """The jobs this candidate scored highest against, best first.

    Returns ``(matches, unscored_jobs)``.

    The scores are the ones the scoring engine already produced for a screening of
    that job — the same numbers the job page shows. Nothing is computed here: a
    fit score for a job this candidate was never screened against would be a new
    number with no run behind it, and the count of those jobs is returned instead
    so the caller can say so rather than imply the list is exhaustive.

    Where a job has been screened more than once, the candidate's best result for
    that job wins — a later run with a wider pool does not make an earlier score
    untrue, and showing both would list one job twice.
    """
    results = (
        session.execute(
            select(ScreeningResult, Job, Screening.finished_at)
            .join(Screening, Screening.id == ScreeningResult.screening_id)
            .join(Job, Job.id == Screening.job_id)
            .options(selectinload(ScreeningResult.explanation))
            .where(ScreeningResult.candidate_id == candidate_id, Job.owner_id == owner_id)
            .order_by(ScreeningResult.composite_score.desc())
        )
        .unique()
        .all()
    )

    # First row per job wins because the query is already ordered by score.
    best_by_job: dict[uuid.UUID, dict] = {}
    for result, job, completed_at in results:
        if job.id in best_by_job:
            continue
        explanation = result.explanation
        best_by_job[job.id] = {
            "job_id": job.id,
            "job_title": job.title,
            "job_status": job.status,
            "department": job.department,
            "location": job.location,
            "score": float(result.composite_score),
            "recommendation": result.override_recommendation or result.recommendation,
            "screening_id": result.screening_id,
            "screened_at": completed_at,
            "shortlisted": bool(result.shortlisted),
            "rank": result.rank,
            # The same breakdown the job page shows, so the two never disagree.
            "subscores": subscore_rows(result.subscores),
            "matched_skills": list(result.matched_skills or []),
            "missing_skills": list(result.missing_skills or []),
            "evidence": dict(result.evidence or {}),
            "explanation": explanation,
        }

    total_jobs = int(
        session.execute(
            select(func.count()).select_from(Job).where(Job.owner_id == owner_id)
        ).scalar()
        or 0
    )
    matches = sorted(best_by_job.values(), key=lambda row: row["score"], reverse=True)
    return matches[:limit], total_jobs - len(best_by_job)


def best_match(session: Session, candidate_id: uuid.UUID, owner_id: uuid.UUID):
    """Highest-scoring screening result for this candidate, or None.

    Returns the job alongside the score: a match score without the role it was
    measured against says nothing a recruiter can act on.
    """
    row = session.execute(
        select(
            ScreeningResult.composite_score,
            func.coalesce(
                ScreeningResult.override_recommendation, ScreeningResult.recommendation
            ),
            Job.id,
            Job.title,
            Screening.id,
        )
        .join(Screening, Screening.id == ScreeningResult.screening_id)
        .join(Job, Job.id == Screening.job_id)
        .where(ScreeningResult.candidate_id == candidate_id, Job.owner_id == owner_id)
        .order_by(ScreeningResult.composite_score.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    return {
        "score": float(row[0]),
        "recommendation": row[1],
        "job_id": row[2],
        "job_title": row[3],
        "screening_id": row[4],
    }

"""Candidate queries, including the hybrid SQL + ANN retrieval.

Funnel stages 1 and 2 (architecture doc §10) are one statement: the hard gates are
SQL predicates and the ordering is a pgvector nearest-neighbour scan. Doing them
together is the reason pgvector was chosen over a separate vector database — the
filter runs where the data lives instead of over-fetching and filtering in Python.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from pgvector.sqlalchemy import Vector
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.core.config import settings

#: Latest profile per candidate, for this recruiter's candidates only. Profiles are
#: versioned and a screening must only ever see the current one.
#:
#: The join to ``candidates`` is what keeps this scoped. Without it the CTE takes the
#: newest profile for every candidate in the table — every tenant's — and the outer
#: query then joins that down to the handful it wanted, so one recruiter's screening
#: got slower as unrelated recruiters uploaded resumes. Measured at 80k profile rows
#: with six candidates under test: 25ms unscoped against 1.8ms scoped, and it runs
#: three times per screening.
#:
#: Every statement below binds ``:owner``; a new one must too.
_LATEST_PROFILE_CTE = """
WITH latest AS (
    SELECT DISTINCT ON (p.candidate_id)
        p.candidate_id,
        p.id AS profile_id,
        p.skills,
        p.domains,
        p.current_title,
        p.total_years_experience,
        p.seniority_rank
    FROM candidate_profiles p
    JOIN candidates owned ON owned.id = p.candidate_id
    WHERE owned.owner_id = :owner
    ORDER BY p.candidate_id, p.version DESC
)
"""

#: Every required importance-5 skill must appear in the profile's skill array,
#: compared case-insensitively so "postgresql" and "PostgreSQL" are one skill.
_SKILL_GATE = """
    AND NOT EXISTS (
        SELECT 1
        FROM unnest(CAST(:must_have AS text[])) AS req(skill)
        WHERE NOT EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(latest.skills) AS owned(skill)
            WHERE lower(owned.skill) = lower(req.skill)
        )
    )
"""


#: Someone already placed is out of the running everywhere. A hire is a fact about
#: the person, not about one job: leaving them in would have them competing for —
#: and displacing real candidates from — every other role on the board.
_PLACED_GATE = """
    AND NOT EXISTS (
        SELECT 1
        FROM applications a
        WHERE a.candidate_id = c.id
          AND a.stage = 'hired'
    )
"""

#: Candidates the recruiter removed from *this* job's rankings. Job-scoped, so the
#: same person still appears for every other role.
_EXCLUSION_GATE = """
    AND NOT EXISTS (
        SELECT 1
        FROM job_candidate_exclusions x
        WHERE x.candidate_id = c.id
          AND x.job_id = :job_id
    )
"""


def _gates(job_id: uuid.UUID | None) -> str:
    """The gates every funnel query shares. ``job_id`` is None only where there is
    no job in play, in which case there is nothing job-scoped to exclude."""
    return _PLACED_GATE + (_EXCLUSION_GATE if job_id is not None else "")


@dataclass
class RetrievedCandidate:
    candidate_id: uuid.UUID
    profile_id: uuid.UUID
    full_name: str
    current_title: str | None
    years: float
    skills: list[str]
    domains: list[str]
    similarity: float


@dataclass
class SurvivorDetail:
    """Full profile record for the candidates that survived reranking.

    Retrieval deliberately selects only what the funnel needs to order candidates;
    the fields the evaluator reasons over are loaded here, for the ~20 survivors
    rather than the ~100 retrieved.
    """

    candidate_id: uuid.UUID
    resume_text: str
    education: list[str]
    certifications: list[str]
    achievements: list[str]
    projects: list[str]
    experience: list[dict]


def count_pool(session: Session, owner_id: uuid.UUID, *, job_id: uuid.UUID | None = None) -> int:
    """Every candidate eligible for this job — the top of the funnel.

    Eligible excludes anyone already hired and, when a job is given, anyone the
    recruiter removed from that job's rankings. The count has to apply the same
    gates as the retrieval below it, or the funnel reports a pool it never searched.
    """
    sql = (
        _LATEST_PROFILE_CTE
        + """
        SELECT count(*)
        FROM candidates c
        JOIN latest ON latest.candidate_id = c.id
        WHERE c.owner_id = :owner AND c.status = 'active'
        """
        + _gates(job_id)
    )
    params: dict[str, object] = {"owner": owner_id}
    if job_id is not None:
        params["job_id"] = job_id
    return int(session.execute(text(sql), params).scalar() or 0)


def count_after_filters(
    session: Session,
    owner_id: uuid.UUID,
    *,
    must_have_skills: list[str],
    min_years: int,
    job_id: uuid.UUID | None = None,
) -> int:
    sql = (
        _LATEST_PROFILE_CTE
        + """
        SELECT count(*)
        FROM candidates c
        JOIN latest ON latest.candidate_id = c.id
        WHERE c.owner_id = :owner
          AND c.status = 'active'
          AND latest.total_years_experience >= :min_years
        """
        + (_SKILL_GATE if must_have_skills else "")
        + _gates(job_id)
    )

    params: dict[str, object] = {"owner": owner_id, "min_years": min_years}
    if must_have_skills:
        params["must_have"] = must_have_skills
    if job_id is not None:
        params["job_id"] = job_id
    return int(session.execute(text(sql), params).scalar() or 0)


def retrieve(
    session: Session,
    owner_id: uuid.UUID,
    *,
    job_vector: list[float],
    embedding_model: str,
    must_have_skills: list[str],
    min_years: int,
    limit: int,
    job_id: uuid.UUID | None = None,
) -> list[RetrievedCandidate]:
    """Funnel stages 1-2: hard filters, then ANN ordering by cosine distance."""
    sql = (
        _LATEST_PROFILE_CTE
        + """
        SELECT
            c.id                            AS candidate_id,
            latest.profile_id               AS profile_id,
            c.full_name                     AS full_name,
            latest.current_title            AS current_title,
            latest.total_years_experience   AS years,
            latest.skills                   AS skills,
            latest.domains                  AS domains,
            1 - (e.vector <=> :qvec)        AS similarity
        FROM candidates c
        JOIN latest ON latest.candidate_id = c.id
        JOIN embeddings e
          ON e.owner_type = 'candidate'
         AND e.owner_id = c.id
         AND e.model = :embedding_model
        WHERE c.owner_id = :owner
          AND c.status = 'active'
          AND latest.total_years_experience >= :min_years
        """
        + (_SKILL_GATE if must_have_skills else "")
        + _gates(job_id)
        + """
        ORDER BY e.vector <=> :qvec
        LIMIT :limit
        """
    )

    # An HNSW index scan returns at most ef_search rows, and `limit` here is
    # retrieval_limit (100) against pgvector's default ef_search of 40 — so if the
    # planner ever picks the ANN path, the funnel would start stage 3 with 40
    # survivors instead of 100 and nothing would say so.
    #
    # Measured: it does not pick that path today. The profile CTE join makes an exact
    # scan cheaper, and this returns a full 100 at ef_search 40. The floor is set
    # anyway because the plan is a cost decision that moves with data distribution,
    # and the failure mode when it moves is a quietly narrower funnel.
    # SET LOCAL reverts with the transaction, so the pooled connection is unaffected.
    ef = max(int(settings.hnsw_ef_search), limit)
    session.execute(text(f"SET LOCAL hnsw.ef_search = {ef}"))

    statement = text(sql).bindparams(
        bindparam("qvec", type_=Vector(settings.embedding_dim))
    )
    params: dict[str, object] = {
        "owner": owner_id,
        "qvec": job_vector,
        "embedding_model": embedding_model,
        "min_years": min_years,
        "limit": limit,
    }
    if must_have_skills:
        params["must_have"] = must_have_skills
    if job_id is not None:
        params["job_id"] = job_id

    rows = session.execute(statement, params).mappings().all()
    return [
        RetrievedCandidate(
            candidate_id=row["candidate_id"],
            profile_id=row["profile_id"],
            full_name=row["full_name"],
            current_title=row["current_title"],
            years=float(row["years"] or 0),
            skills=list(row["skills"] or []),
            domains=list(row["domains"] or []),
            similarity=float(row["similarity"] or 0.0),
        )
        for row in rows
    ]


def load_survivor_details(
    session: Session, candidate_ids: list[uuid.UUID]
) -> dict[uuid.UUID, SurvivorDetail]:
    """Latest resume text plus the latest profile's evidence fields, per candidate.

    Two DISTINCT ON scans rather than a join across both versioned tables: resume
    version and profile version advance independently, and joining them risks
    pairing a new resume with a stale profile.
    """
    if not candidate_ids:
        return {}

    ids = [str(cid) for cid in candidate_ids]

    resume_rows = session.execute(
        text(
            """
            SELECT DISTINCT ON (r.candidate_id) r.candidate_id, r.extracted_text
            FROM resumes r
            WHERE r.candidate_id = ANY(CAST(:ids AS uuid[]))
              AND r.extracted_text IS NOT NULL
            ORDER BY r.candidate_id, r.version DESC
            """
        ),
        {"ids": ids},
    ).mappings()
    texts = {row["candidate_id"]: row["extracted_text"] or "" for row in resume_rows}

    profile_rows = session.execute(
        text(
            """
            SELECT DISTINCT ON (p.candidate_id)
                p.candidate_id, p.education, p.certifications,
                p.achievements, p.projects, p.experience
            FROM candidate_profiles p
            WHERE p.candidate_id = ANY(CAST(:ids AS uuid[]))
            ORDER BY p.candidate_id, p.version DESC
            """
        ),
        {"ids": ids},
    ).mappings()

    details: dict[uuid.UUID, SurvivorDetail] = {}
    for row in profile_rows:
        cid = row["candidate_id"]
        details[cid] = SurvivorDetail(
            candidate_id=cid,
            resume_text=texts.get(cid, ""),
            education=list(row["education"] or []),
            certifications=list(row["certifications"] or []),
            achievements=list(row["achievements"] or []),
            projects=list(row["projects"] or []),
            experience=list(row["experience"] or []),
        )
    return details

"""Search sessions and paging (architecture doc §13).

"Show me other profiles" is answered from persisted state, never from model memory.
A session pins one ranked list at the moment it is created; each page flips ``shown``
on the rows it returns, and the next page excludes them. Three consequences matter:

- Paging is reproducible. Re-running the funnel between pages could reorder the list
  and show the same candidate twice, or skip one entirely.
- Paging is cheap. The expensive work happened once, during the screening.
- Paging is auditable. Which candidates a recruiter was actually shown, and when, is
  a stored fact rather than an inference from logs.

Every query is scoped by ``recruiter_id`` taken from the authenticated identity —
never from the request body (§6).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import (
    Candidate,
    Job,
    Screening,
    ScreeningResult,
    SearchResult,
    SearchSession,
    SearchSessionStatus,
)
from app.services import audit_service

logger = get_logger(__name__)


class SearchSessionNotFound(LookupError):
    """No such session for this recruiter. Deliberately not distinguished from a
    session owned by someone else — telling them apart enables id enumeration (§6)."""


@dataclass
class SearchPage:
    session: SearchSession
    results: list[SearchResult]
    candidates: dict[uuid.UUID, Candidate]
    total: int
    remaining: int


def create_session(
    session: Session,
    *,
    recruiter_id: uuid.UUID,
    screening: Screening,
    page_size: int | None = None,
    applied_filters: dict | None = None,
    relaxations: list[str] | None = None,
) -> SearchSession:
    """Snapshot a completed screening's ranking as a pageable session.

    A screening carries no owner column of its own — ownership lives on the job — so
    the check re-reads the job scoped to this recruiter rather than trusting the
    screening that was handed in.
    """
    owning_job = session.execute(
        select(Job).where(Job.id == screening.job_id, Job.owner_id == recruiter_id)
    ).scalars().first()
    if owning_job is None:
        raise SearchSessionNotFound("Screening does not belong to this recruiter.")

    rows = session.execute(
        select(ScreeningResult)
        .where(ScreeningResult.screening_id == screening.id)
        .order_by(ScreeningResult.rank)
    ).scalars().all()

    search_session = SearchSession(
        recruiter_id=recruiter_id,
        job_id=screening.job_id,
        screening_id=screening.id,
        page_size=page_size or settings.shortlist_size,
        applied_filters=applied_filters or {},
        relaxations=relaxations or list(screening.degradations or []),
    )
    session.add(search_session)
    session.flush()

    for row in rows:
        session.add(
            SearchResult(
                session_id=search_session.id,
                candidate_id=row.candidate_id,
                screening_result_id=row.id,
                rank=row.rank,
                score=row.composite_score,
                shown=False,
            )
        )
    session.commit()

    audit_service.record(
        session,
        "search.run",
        recruiter_id=recruiter_id,
        resource_type="search_session",
        resource_id=search_session.id,
        job_id=screening.job_id,
        ranked=len(rows),
    )
    return search_session


def get_session(
    session: Session, session_id: uuid.UUID, recruiter_id: uuid.UUID
) -> SearchSession:
    """Ownership is enforced in the query, not checked after the fact."""
    found = session.execute(
        select(SearchSession).where(
            SearchSession.id == session_id,
            SearchSession.recruiter_id == recruiter_id,
        )
    ).scalars().first()
    if found is None:
        raise SearchSessionNotFound(f"No search session {session_id} for this recruiter.")
    return found


def _counts(session: Session, session_id: uuid.UUID) -> tuple[int, int]:
    total = session.execute(
        select(func.count()).select_from(SearchResult).where(
            SearchResult.session_id == session_id
        )
    ).scalar_one()
    remaining = session.execute(
        select(func.count()).select_from(SearchResult).where(
            SearchResult.session_id == session_id,
            SearchResult.shown.is_(False),
        )
    ).scalar_one()
    return int(total), int(remaining)


def _hydrate(session: Session, rows: list[SearchResult]) -> dict[uuid.UUID, Candidate]:
    if not rows:
        return {}
    ids = [row.candidate_id for row in rows]
    candidates = session.execute(
        select(Candidate).where(Candidate.id.in_(ids))
    ).scalars().all()
    return {candidate.id: candidate for candidate in candidates}


def current_page(
    session: Session, search_session: SearchSession
) -> SearchPage:
    """Re-read the page already shown, without advancing. Safe to call repeatedly."""
    rows = session.execute(
        select(SearchResult)
        .where(SearchResult.session_id == search_session.id, SearchResult.shown.is_(True))
        .order_by(SearchResult.rank)
    ).scalars().all()
    total, remaining = _counts(session, search_session.id)
    return SearchPage(
        session=search_session,
        results=rows,
        candidates=_hydrate(session, rows),
        total=total,
        remaining=remaining,
    )


def next_page(
    session: Session, search_session: SearchSession, *, limit: int | None = None
) -> SearchPage:
    """Return the next unshown candidates and mark them shown.

    An exhausted session returns an empty page rather than raising: "no more
    profiles" is a normal answer to "show me other profiles", not an error.
    """
    size = limit or search_session.page_size
    rows = session.execute(
        select(SearchResult)
        .where(SearchResult.session_id == search_session.id, SearchResult.shown.is_(False))
        .order_by(SearchResult.rank)
        .limit(size)
    ).scalars().all()

    now = datetime.now(UTC)
    for row in rows:
        row.shown = True
        row.shown_at = now

    # The session factory sets autoflush=False, so these updates are invisible to the
    # count query until flushed — without this the caller is told one page too many
    # remain, and the session is marked exhausted a page late.
    session.flush()
    total, remaining = _counts(session, search_session.id)
    if remaining == 0:
        search_session.status = SearchSessionStatus.EXHAUSTED.value
    session.commit()

    audit_service.record(
        session,
        "search.paged",
        recruiter_id=search_session.recruiter_id,
        resource_type="search_session",
        resource_id=search_session.id,
        returned=len(rows),
        remaining=remaining,
    )
    return SearchPage(
        session=search_session,
        results=rows,
        candidates=_hydrate(session, rows),
        total=total,
        remaining=remaining,
    )

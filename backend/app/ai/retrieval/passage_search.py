"""Passage-level retrieval over resume text.

The counterpart to ``vector_search``: that one ranks whole candidates against a whole
job, this one finds the sentences that answer a question. Same store, same embedder,
same rule that only vectors from one model are ever compared.

Indexing lives here too, so the text that goes in and the query that comes out cannot
drift apart in how they are prepared.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from pgvector.sqlalchemy import Vector
from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.orm import Session

from app.ai.embeddings import get_embedder
from app.ai.retrieval.chunking import chunk_resume
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Embedding, EmbeddingOwner, Resume, ResumeChunk

logger = get_logger(__name__)


@dataclass
class Passage:
    candidate_id: uuid.UUID
    candidate_name: str
    resume_id: uuid.UUID
    ordinal: int
    text: str
    similarity: float


def index_resume(session: Session, resume: Resume, candidate_id: uuid.UUID) -> int:
    """(Re)build the passages and vectors for one resume. Returns the chunk count.

    Replaces rather than appends: a re-ingested resume must not leave the previous
    version's passages behind to be retrieved as if they were current. The embeddings
    go through a bulk ``embed_many`` because a resume is a handful of chunks and one
    call per chunk turns ingestion into N round trips.
    """
    chunks = chunk_resume(resume.extracted_text or "")

    # Drop the old passages and their vectors first. The vectors are keyed by chunk
    # id, so they have to go before the rows they point at.
    stale = session.execute(
        select(ResumeChunk.id).where(ResumeChunk.resume_id == resume.id)
    ).scalars().all()
    if stale:
        session.execute(
            delete(Embedding).where(
                Embedding.owner_type == EmbeddingOwner.RESUME_CHUNK.value,
                Embedding.owner_id.in_(stale),
            )
        )
        session.execute(delete(ResumeChunk).where(ResumeChunk.resume_id == resume.id))

    if not chunks:
        # An image-only or empty resume. Nothing to retrieve, and that is not an
        # error — the candidate is still screenable on their parsed profile.
        logger.info("Resume %s produced no passages to index", resume.id)
        return 0

    rows = [
        ResumeChunk(
            resume_id=resume.id, candidate_id=candidate_id, ordinal=ordinal, text=chunk
        )
        for ordinal, chunk in enumerate(chunks)
    ]
    session.add_all(rows)
    session.flush()

    embedder = get_embedder()
    vectors = embedder.embed_many(chunks)
    session.add_all(
        Embedding(
            owner_type=EmbeddingOwner.RESUME_CHUNK.value,
            owner_id=row.id,
            vector=vector,
            model=embedder.model,
            dim=embedder.dim,
        )
        for row, vector in zip(rows, vectors, strict=True)
    )
    return len(rows)


#: ANN rows pulled per requested passage when collapsing to one per candidate.
#:
#: A resume chunks into 6-11 passages, and a candidate whose whole career is on
#: topic matches most of them. Without overfetch the window fills with one person
#: and the second-best candidate never appears at all.
CANDIDATE_OVERFETCH = 8

#: Ceiling on the ANN window regardless of overfetch. Each row carries ~700
#: characters of resume text and the window is materialised here to collapse and
#: count it, so this is what bounds the memory one search can take.
MAX_WINDOW = 200


@dataclass
class PassageResults:
    """Passages, plus what the search saw while finding them.

    The counts answer a question the passage list cannot. "Has anyone done X" is
    answerable from the passages themselves; "how many people have" is not, because
    the list is capped and one candidate can occupy several of its slots.
    """

    passages: list[Passage]
    #: Distinct candidates in the ANN window that cleared the similarity floor.
    matched_candidates: int
    #: True when the window filled to its limit. More matches may exist beyond it,
    #: so ``matched_candidates`` is then a lower bound rather than a total.
    truncated: bool


def _widen_hnsw_walk(session: Session, window: int) -> None:
    """Raise HNSW's search breadth to at least ``window`` for the rest of this
    transaction.

    An HNSW index scan cannot return more rows than its search list holds, so a query
    whose LIMIT exceeds ``ef_search`` silently comes back short. That is not a
    hypothetical here: collapsing to one passage per candidate means overfetching a
    window several times ``limit``, and pgvector's default ef_search is 40. Measured
    on 5,400 chunk vectors, a window of 80 at ef_search 40 returned 40 rows and half
    of the exact top-80; at 100 and above it returned all of them. The ``max`` below
    is what keeps the window and the walk from disagreeing.

    Whether the planner uses HNSW at all depends on how selective the owner filter
    is — with a small share of the table it prefers an exact scan and sort, which is
    both correct and unaffected by this. The floor is set regardless, because that
    choice flips with data distribution and the failure is silent when it does.

    ``SET LOCAL`` rather than a session-wide ``SET``: it reverts when the transaction
    ends, so a search cannot leave the pooled connection retuned for whatever runs on
    it next.

    Safe to run before any vector operation on a fresh connection, which is where it
    lands: pgvector only defines ``hnsw.ef_search`` when its library loads, and the
    library loads lazily on first vector use. Postgres accepts a SET of an as-yet
    undefined *qualified* name as a placeholder and applies it once the extension
    arrives — verified against pgvector 0.8.6. ``SHOW hnsw.ef_search`` does still
    fail until then, so read it back only after a vector operation.
    """
    ef = max(int(settings.hnsw_ef_search), window)
    # Interpolated rather than bound because SET LOCAL takes no parameters. `ef` is
    # an int by construction, so there is nothing here to inject.
    session.execute(text(f"SET LOCAL hnsw.ef_search = {ef}"))


def search_passages(
    session: Session,
    *,
    owner_id: uuid.UUID,
    query: str,
    candidate_id: uuid.UUID | None = None,
    limit: int = 5,
    min_similarity: float | None = None,
    one_per_candidate: bool = True,
) -> PassageResults:
    """The passages closest to ``query``, best first — or none, if none are close.

    Scoped to ``owner_id`` in SQL, not by a filter the caller may forget: a passage
    is raw resume text, so a leak here would hand one recruiter another's candidate
    verbatim. ``candidate_id`` narrows it to one person for "what did X say about Y".

    Results below the embedder's ``min_similarity`` are dropped. Without that an ANN
    search always returns ``limit`` rows, so asking about work nobody has done gets
    the least-unrelated passages back and they read as though they were answers.
    ``min_similarity`` overrides the default for a caller that wants everything.

    ``one_per_candidate`` keeps only each candidate's best passage, so ``limit``
    counts people rather than paragraphs — without it one candidate whose resume is
    entirely on topic takes every slot. It is ignored when ``candidate_id`` is set,
    where collapsing would reduce "what did X say about Y" to a single passage.
    """
    if not query.strip():
        return PassageResults(passages=[], matched_candidates=0, truncated=False)

    embedder = get_embedder()
    # embed_query, not embed: a question and the passage answering it are different
    # kinds of text, and providers with asymmetric retrieval modes want to be told.
    query_vector = embedder.embed_query(query)
    floor = embedder.min_similarity if min_similarity is None else min_similarity

    collapse = one_per_candidate and candidate_id is None
    window = min(limit * CANDIDATE_OVERFETCH, MAX_WINDOW) if collapse else limit

    _widen_hnsw_walk(session, window)

    sql = """
        SELECT
            k.candidate_id                  AS candidate_id,
            c.full_name                     AS candidate_name,
            k.resume_id                     AS resume_id,
            k.ordinal                       AS ordinal,
            k.text                          AS text,
            1 - (e.vector <=> :qvec)        AS similarity
        FROM resume_chunks k
        JOIN candidates c ON c.id = k.candidate_id
        JOIN embeddings e
          ON e.owner_type = 'resume_chunk'
         AND e.owner_id = k.id
         AND e.model = :model
        WHERE c.owner_id = :owner
          AND c.status = 'active'
    """
    if candidate_id is not None:
        sql += " AND k.candidate_id = :candidate\n"
    # Floor applied in SQL so the window is spent on rows that qualify, rather than
    # fetching it and then discarding most of them.
    sql += " AND (1 - (e.vector <=> :qvec)) >= :floor "
    sql += " ORDER BY e.vector <=> :qvec LIMIT :window"

    statement = text(sql).bindparams(
        bindparam("qvec", type_=Vector(settings.embedding_dim))
    )
    params: dict[str, object] = {
        "qvec": query_vector,
        "model": embedder.model,
        "owner": owner_id,
        "window": window,
        "floor": floor,
    }
    if candidate_id is not None:
        params["candidate"] = candidate_id

    rows = session.execute(statement, params).mappings().all()

    # Counted over the whole window rather than the returned slice, because the
    # count exists to answer "how many people" and the capped slice cannot.
    matched_candidates = len({row["candidate_id"] for row in rows})

    passages: list[Passage] = []
    represented: set[uuid.UUID] = set()
    for row in rows:  # already ordered by distance, best first
        if len(passages) >= limit:
            break
        if collapse and row["candidate_id"] in represented:
            continue
        represented.add(row["candidate_id"])
        passages.append(
            Passage(
                candidate_id=row["candidate_id"],
                candidate_name=row["candidate_name"],
                resume_id=row["resume_id"],
                ordinal=row["ordinal"],
                text=row["text"],
                similarity=round(float(row["similarity"]), 4),
            )
        )

    return PassageResults(
        passages=passages,
        matched_candidates=matched_candidates,
        truncated=len(rows) >= window,
    )


def search(
    session: Session,
    *,
    owner_id: uuid.UUID,
    query: str,
    candidate_id: uuid.UUID | None = None,
    limit: int = 5,
    min_similarity: float | None = None,
    one_per_candidate: bool = True,
) -> list[Passage]:
    """Just the passages. ``search_passages`` returns the same list with the
    candidate counts alongside it."""
    return search_passages(
        session,
        owner_id=owner_id,
        query=query,
        candidate_id=candidate_id,
        limit=limit,
        min_similarity=min_similarity,
        one_per_candidate=one_per_candidate,
    ).passages

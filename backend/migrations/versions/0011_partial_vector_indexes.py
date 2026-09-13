"""one HNSW index per vector kind instead of one shared graph

``embeddings`` holds three kinds of vector discriminated by ``owner_type``, and 0001
put a single HNSW index over all of them. Both ANN queries in the system filter to
one kind — ``owner_type = 'candidate'`` in the funnel's retrieve, ``'resume_chunk'``
in passage search — but HNSW cannot apply that predicate during the graph walk, so
the walk visits vectors of every kind and the WHERE clause discards the wrong ones
afterwards. A resume chunks into 6-11 passages against one candidate vector, so
chunk vectors outnumber candidate vectors by an order of magnitude and a candidate
search walks mostly chunks.

Each partial index has a predicate matching one query's ``owner_type`` filter
exactly, so a walk only ever visits vectors of the kind being searched. Verified in
use: on 5,400 chunk vectors held by one recruiter, passage search plans an
``Index Scan using ix_embeddings_vector_hnsw_chunk``. With a *small* share of the
table the planner instead prefers an exact scan and sort, which is correct and
faster there — so this is insurance for the shape where ANN wins, not a speedup for
every query.

Job vectors are read by id and never ANN-searched, so they get no index at all — the
shared one was only ever costing them insert time.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-10
"""
from __future__ import annotations

from alembic import op

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None

#: The owner_types that are ANN-searched, and the index serving each. Kept as data
#: so upgrade and downgrade cannot disagree about the set.
SEARCHED_KINDS = (
    ("ix_embeddings_vector_hnsw_candidate", "candidate"),
    ("ix_embeddings_vector_hnsw_chunk", "resume_chunk"),
)


def upgrade() -> None:
    # NOTE: as in 0006-0010 — ix_profiles_skills_gin is a raw-SQL index from 0001
    # that autogenerate cannot see and proposes dropping every time. Not touched
    # here. ix_embeddings_vector_hnsw *is* deliberately replaced below.
    for name, owner_type in SEARCHED_KINDS:
        op.execute(
            f"CREATE INDEX {name} ON embeddings "
            "USING hnsw (vector vector_cosine_ops) "
            f"WHERE owner_type = '{owner_type}'"
        )

    # Dropped last: until the partial indexes exist, this is the only thing keeping
    # retrieval off a sequential scan.
    op.execute("DROP INDEX IF EXISTS ix_embeddings_vector_hnsw")


def downgrade() -> None:
    op.execute(
        "CREATE INDEX ix_embeddings_vector_hnsw ON embeddings "
        "USING hnsw (vector vector_cosine_ops)"
    )
    for name, _owner_type in SEARCHED_KINDS:
        op.execute(f"DROP INDEX IF EXISTS {name}")

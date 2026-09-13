"""stop embeddings outliving what they describe

``embeddings.owner_id`` is polymorphic — one table serves candidates, jobs and now
resume passages, discriminated by ``owner_type`` — so it cannot carry a foreign key
and nothing cascades to it. Deleting a candidate or a job therefore left its vector
behind forever. Measured before this ran: 8,265 of 8,422 rows were orphans, 86 MB
where the live data is under 2 MB, and every one of them sat in the HNSW index that
each screening's ANN search traverses.

Fixed with database triggers rather than application code. Candidates and jobs are
removed by ``ON DELETE CASCADE`` from ``users``, so the delete happens inside the
database and no Python ever runs — a service-level hook would miss the path that
causes almost all of the accumulation.

Statement-level with a transition table, not per row: a cascade removes many rows in
one statement, and this way that is one DELETE against embeddings instead of N.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-09
"""
from __future__ import annotations

from alembic import op

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None

#: The tables whose rows own an embedding, with the owner_type each writes.
OWNERS = (
    ("candidates", "candidate"),
    ("jobs", "job"),
    ("resume_chunks", "resume_chunk"),
)


def upgrade() -> None:
    # NOTE: as in 0006-0009 — ix_profiles_skills_gin and ix_embeddings_vector_hnsw
    # are raw-SQL indexes from 0001 that autogenerate cannot see and proposes
    # dropping every time. Not touched here.

    op.execute(
        """
        CREATE OR REPLACE FUNCTION drop_orphaned_embeddings() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            DELETE FROM embeddings e
            USING deleted d
            WHERE e.owner_type = TG_ARGV[0]
              AND e.owner_id = d.id;
            RETURN NULL;
        END;
        $$;
        """
    )

    for table, owner_type in OWNERS:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_drop_embeddings
            AFTER DELETE ON {table}
            REFERENCING OLD TABLE AS deleted
            FOR EACH STATEMENT
            EXECUTE FUNCTION drop_orphaned_embeddings('{owner_type}');
            """
        )

    # Clear what has already accumulated. Only rows whose owner is genuinely gone:
    # a vector from a superseded embedding model whose owner still exists is inert
    # rather than orphaned, and keeping it is what makes switching models back
    # possible without re-embedding.
    op.execute(
        """
        DELETE FROM embeddings e
        WHERE (e.owner_type = 'candidate'
               AND NOT EXISTS (SELECT 1 FROM candidates c WHERE c.id = e.owner_id))
           OR (e.owner_type = 'job'
               AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.id = e.owner_id))
           OR (e.owner_type = 'resume_chunk'
               AND NOT EXISTS (SELECT 1 FROM resume_chunks k WHERE k.id = e.owner_id))
        """
    )

    # The delete leaves dead tuples in the HNSW index, which an ANN scan still walks.
    # REINDEX runs inside a transaction; VACUUM FULL cannot, so reclaiming the file
    # space is a separate manual step and is only about disk, not query speed.
    op.execute("REINDEX INDEX ix_embeddings_vector_hnsw")


def downgrade() -> None:
    for table, _owner_type in OWNERS:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_drop_embeddings ON {table}")
    op.execute("DROP FUNCTION IF EXISTS drop_orphaned_embeddings()")
    # Deleted orphans are not restored: they described rows that no longer exist.

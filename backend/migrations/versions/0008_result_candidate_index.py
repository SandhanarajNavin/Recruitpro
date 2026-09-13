"""index screening_results by candidate

Every "what has this person scored" read filters on candidate_id: their best-fit
jobs, the best-match panel, the best-score column on the candidate list, and the
origin lookup when the assistant shortlists someone. The existing indexes are
(screening_id, rank) and (screening_id, candidate_id), and neither can serve a
candidate_id-only predicate — those reads were scanning.

composite_score DESC rides along because every one of those callers wants the best
score first, so the ordering comes from the index instead of a sort.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-09
"""
from __future__ import annotations

from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NOTE: as in 0006 and 0007 — ix_profiles_skills_gin and
    # ix_embeddings_vector_hnsw are raw-SQL indexes from 0001 that autogenerate
    # cannot see and proposes dropping every time. Not touched here.
    op.execute(
        "CREATE INDEX ix_results_candidate_score "
        "ON screening_results (candidate_id, composite_score DESC)"
    )


def downgrade() -> None:
    op.drop_index("ix_results_candidate_score", "screening_results")

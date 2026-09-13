"""candidates removed from one job's rankings

A recruiter can take a candidate out of a single job's results without touching the
candidate or their other searches. Job-scoped rather than global: removing someone
from one role says nothing about their fit for another.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NOTE: as in 0006 — ix_profiles_skills_gin and ix_embeddings_vector_hnsw are
    # raw-SQL indexes from 0001 that autogenerate cannot see and proposes dropping
    # every time. Not touched here.
    op.create_table(
        "job_candidate_exclusions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "excluded_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("job_id", "candidate_id", name="uq_exclusion_job_candidate"),
    )
    op.create_index("ix_job_candidate_exclusions_job_id", "job_candidate_exclusions", ["job_id"])
    op.create_index(
        "ix_job_candidate_exclusions_candidate_id", "job_candidate_exclusions", ["candidate_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_job_candidate_exclusions_candidate_id", "job_candidate_exclusions")
    op.drop_index("ix_job_candidate_exclusions_job_id", "job_candidate_exclusions")
    op.drop_table("job_candidate_exclusions")

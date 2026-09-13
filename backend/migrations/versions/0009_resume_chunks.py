"""retrievable passages of a resume

The candidate vector is built from parsed fields, so nothing in the system could
answer a question about what a resume actually says. These rows hold the resume's own
words, split into passages, with one embedding each in the existing embeddings table
under owner_type 'resume_chunk'.

owner_type is a varchar rather than a database enum, so the new kind needs no type
change — only this table.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NOTE: as in 0006-0008 — ix_profiles_skills_gin and ix_embeddings_vector_hnsw
    # are raw-SQL indexes from 0001 that autogenerate cannot see and proposes
    # dropping every time. Not touched here.
    op.create_table(
        "resume_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "resume_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("resumes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("resume_id", "ordinal", name="uq_chunk_resume_ordinal"),
    )
    op.create_index("ix_resume_chunks_resume_id", "resume_chunks", ["resume_id"])
    op.create_index("ix_resume_chunks_candidate", "resume_chunks", ["candidate_id"])


def downgrade() -> None:
    op.drop_index("ix_resume_chunks_candidate", "resume_chunks")
    op.drop_index("ix_resume_chunks_resume_id", "resume_chunks")
    op.drop_table("resume_chunks")

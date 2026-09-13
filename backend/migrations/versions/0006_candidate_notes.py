"""recruiter notes on a candidate

Free text the recruiter writes, kept apart from anything the parser produces so a
reprocess never overwrites it. Nullable rather than defaulted: "no notes" and "an
empty note" are different states, and only the first should render as absent.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NOTE: ix_profiles_skills_gin and ix_embeddings_vector_hnsw are raw-SQL
    # indexes from 0001. Autogenerate cannot see them and proposes dropping them
    # on every revision; dropping the HNSW one turns retrieval into a sequential
    # scan. Stripped again here — fourth revision running.
    op.add_column("candidates", sa.Column("notes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("candidates", "notes")

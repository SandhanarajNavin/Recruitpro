"""Initial schema: users, candidate repository, jobs, screenings, embeddings.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

EMBEDDING_DIM = 1536


def upgrade() -> None:
    # pgvector lives in the same database as everything else — that is the point of
    # choosing it over a separate vector store for V1.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="recruiter"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320)),
        sa.Column("phone", sa.String(64)),
        sa.Column("location", sa.String(160)),
        sa.Column("summary", sa.Text()),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_candidates_email", "candidates", ["email"])
    op.create_index("ix_candidates_owner_status", "candidates", ["owner_id", "status"])

    op.create_table(
        "resumes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(160), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(64)),
        sa.Column("extracted_text", sa.Text()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("error", sa.Text()),
        sa.Column("processed_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_resumes_checksum", "resumes", ["checksum"])
    op.create_index("ix_resumes_status", "resumes", ["status"])
    op.create_index("ix_resumes_candidate_version", "resumes", ["candidate_id", "version"])

    op.create_table(
        "candidate_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resume_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("current_title", sa.String(200)),
        sa.Column("total_years_experience", sa.Numeric(4, 1), nullable=False, server_default="0"),
        sa.Column("seniority_rank", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("skills", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("domains", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("experience", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("education", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("certifications", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("projects", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("achievements", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("parser_model", sa.String(120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_profiles_candidate_version", "candidate_profiles", ["candidate_id", "version"])
    # Containment index for the skill gate in the retrieval query.
    op.execute(
        "CREATE INDEX ix_profiles_skills_gin ON candidate_profiles USING gin (skills jsonb_path_ops)"
    )

    op.create_table(
        "embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_type", sa.String(16), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vector", pgvector.sqlalchemy.Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_type", "owner_id", "model", name="uq_embedding_owner_model"),
    )
    op.create_index("ix_embeddings_owner", "embeddings", ["owner_type", "owner_id"])
    # HNSW over cosine distance. Deliberately not IVFFlat: IVFFlat needs training
    # data to build a useful index, and an empty repository is exactly the state
    # this migration runs in.
    op.execute(
        "CREATE INDEX ix_embeddings_vector_hnsw ON embeddings "
        "USING hnsw (vector vector_cosine_ops)"
    )

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("location", sa.String(160)),
        sa.Column("status", sa.String(24), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_jobs_owner_id", "jobs", ["owner_id"])

    op.create_table(
        "job_requirements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("seniority", sa.String(80)),
        sa.Column("min_years_experience", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("required_skills", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("preferred_skills", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("domains", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("responsibilities", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("education", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("red_flags", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("hard_filters", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("parser_model", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "screenings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("mode", sa.String(16), nullable=False, server_default="offline"),
        sa.Column("pool_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("filtered_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retrieved_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reranked_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evaluated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("weights", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("panel_summary", sa.Text()),
        sa.Column("degradations", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_screenings_job_id", "screenings", ["job_id"])
    op.create_index("ix_screenings_status", "screenings", ["status"])

    op.create_table(
        "screening_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("screening_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("screenings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("candidate_profiles.id", ondelete="SET NULL")),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("composite_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("subscores", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("matched_skills", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("missing_skills", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("retrieval_score", sa.Numeric(6, 4)),
        sa.Column("rerank_score", sa.Numeric(6, 2)),
        sa.Column("shortlisted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("recommendation", sa.String(24), nullable=False, server_default="pass"),
        sa.Column("override_recommendation", sa.String(24)),
        sa.Column("override_note", sa.Text()),
        sa.Column("override_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("screening_id", "candidate_id", name="uq_result_screening_candidate"),
    )
    op.create_index("ix_results_screening_rank", "screening_results", ["screening_id", "rank"])

    op.create_table(
        "explanations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("screening_result_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("screening_results.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("why_match", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("why_not", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("verdict", sa.Text()),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("explanations")
    op.drop_table("screening_results")
    op.drop_table("screenings")
    op.drop_table("job_requirements")
    op.drop_table("jobs")
    op.drop_table("embeddings")
    op.drop_table("candidate_profiles")
    op.drop_table("resumes")
    op.drop_table("candidates")
    op.drop_table("users")

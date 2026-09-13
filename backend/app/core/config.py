"""Application settings.

Everything tunable lives here so a deployment can change behaviour without a code
change. Notably the scoring weights (architecture doc §11) and the funnel widths
(§10) are configuration, not constants buried in the pipeline.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScoringWeights(BaseSettings):
    """Weighted factors from architecture doc §12 (table 6). Must sum to 1.0.

    Role and industry replaced the earlier responsibilities/education split: the
    design document scores role compatibility and industry fit as first-class
    factors, and folds responsibility alignment into the role judgement.
    """

    required_skills: float = 0.40
    experience: float = 0.25
    role: float = 0.15
    industry: float = 0.10
    semantic_similarity: float = 0.10

    model_config = SettingsConfigDict(env_prefix="WEIGHT_")

    def as_dict(self) -> dict[str, float]:
        return {
            "required_skills": self.required_skills,
            "experience": self.experience,
            "role": self.role,
            "industry": self.industry,
            "semantic_similarity": self.semantic_similarity,
        }

    def total(self) -> float:
        return round(sum(self.as_dict().values()), 6)


#: backend — this file is app/core/config.py, so two levels up from the package.
API_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Anchored to the package, not the working directory: uvicorn and celery are
        # routinely launched from the repo root, and a relative env_file silently
        # resolves to the wrong file there — which means falling back to default
        # connection settings instead of failing loudly.
        env_file=(API_ROOT / ".env", API_ROOT / ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── app ───────────────────────────────────────────────────────────────
    app_name: str = "Recruitment Agent API"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"
    # Explicit production allowlist. Override with
    # CORS_ORIGINS="https://app.example.com" in any real deployment.
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    # Dev convenience: Next.js walks to the next free port when 3000 is taken (this
    # machine has another stack on 3000, so it lands on 3001/3002/...), and a
    # browser treats localhost and 127.0.0.1 as different origins. Enumerating ports
    # is a losing game, so any loopback origin is allowed while
    # environment == "development". Set CORS_ALLOW_LOOPBACK=false to turn it off.
    cors_allow_loopback: bool = True

    # Used only by ``python -m app.main``. Deployments pass these to uvicorn or
    # gunicorn directly, which is why nothing else reads them.
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_reload: bool = True

    # ── database ──────────────────────────────────────────────────────────
    database_url: str = "postgresql+psycopg://recruitment:recruitment@localhost:5432/recruitment"
    db_echo: bool = False
    # Fail fast when the database is unreachable instead of hanging the request.
    db_connect_timeout: int = 5

    # ── queue ─────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    # When true, tasks execute inline in the calling process instead of going to a
    # worker. Lets the whole system run with no Redis and no worker for local work.
    task_always_eager: bool = False

    # ── auth ──────────────────────────────────────────────────────────────
    # Open signup is right for a demo and wrong for a tenant-isolated production
    # deployment, where accounts should be provisioned. Off is one env var away.
    allow_self_signup: bool = True

    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 12

    # ── storage ───────────────────────────────────────────────────────────
    # "local" writes under storage_local_dir; "s3" uses the bucket settings.
    storage_backend: str = "local"
    storage_local_dir: Path = API_ROOT / "uploads"
    s3_bucket: str | None = None
    s3_region: str | None = None
    max_upload_bytes: int = 10 * 1024 * 1024
    allowed_upload_types: list[str] = Field(
        default_factory=lambda: [
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/plain",
        ]
    )

    # ── AI layer (architecture doc §16, §29: Gemini on Vertex AI) ─────────
    # Two ways to reach Gemini. Vertex bills to the GCP project and is what the
    # design document specifies; the Developer API key is the low-ceremony path for
    # local work. Vertex wins when both are configured.
    google_genai_use_vertexai: bool = True
    google_cloud_project: str | None = None
    google_cloud_location: str = "us-central1"
    google_api_key: str | None = None

    # flash, not pro, and deliberately. Measured on one candidate, three runs:
    #   pro   + thinking(-1) : 93.60 / 94.85 / 96.35, 34-41s  -> 2.75 point spread
    #   flash + thinking(0)  : 93.15 / 93.15 / 93.15, 13-16s  -> exactly reproducible
    # A shortlist whose scores move between runs cannot be defended to a hiring
    # manager, and §12 stakes the design on reproducibility. Set RECRUITER_MODEL=
    # gemini-2.5-pro for more nuanced judgement, accepting both costs.
    recruiter_model: str = "gemini-2.5-flash"
    # The mechanical extraction passes do not need the strongest model (§25).
    extraction_model: str = "gemini-2.5-flash"
    #: Gemini thinking budget in tokens. -1 lets the model decide, 0 disables.
    #: 0 is what makes evaluation reproducible: the reasoning trace is the remaining
    #: source of run-to-run variance once temperature and seed are pinned.
    #: NOTE: gemini-2.5-pro rejects 0 outright ("model does not support setting
    #: thinking_budget to 0"), and the request then falls back to the rules engine.
    #: Raise this if you switch recruiter_model to pro.
    recruiter_thinking_budget: int = 0
    extraction_thinking_budget: int = 0

    #: 0 makes sampling greedy. Without it Gemini defaults to ~1.0 and the same
    #: candidate scores differently on every run — fatal for a system whose whole
    #: claim is that a rank is reproducible from stored evidence (doc §12).
    llm_temperature: float = 0.0

    #: Fixed sampling seed. Combined with temperature 0 this removes the sampling
    #: source of variance. It does NOT make a thinking model fully deterministic —
    #: see the note in llm_service — so treat it as variance reduction, not a
    #: guarantee. Set to -1 to omit the parameter entirely.
    llm_seed: int = 42

    #: Candidates evaluated concurrently. Each is an independent model call, so the
    #: funnel was spending N x latency waiting in series.
    evaluation_concurrency: int = 6

    # Embeddings. The offline hashing embedder is dimension-compatible with
    # 1536-dimension hosted models, so swapping providers is a backfill rather than
    # a migration.
    # offline | openai. A Vertex embedder is NOT implemented yet — the funnel
    # still uses the hashing embedder unless OpenAI is configured.
    #: auto | gemini | openai | offline. "auto" uses Gemini when Google credentials
    #: are configured and the offline hashing embedder otherwise, mirroring
    #: reranker_provider below.
    embedding_provider: str = "auto"
    #: Must match the provider. gemini-embedding-001 is native 3072 but supports a
    #: truncated 1536, which is why embedding_dim can stay as it is — changing the
    #: dim needs a migration, since the vector column is fixed width.
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 1536

    #: HNSW search breadth, applied per ANN query with ``SET LOCAL``.
    #:
    #: An HNSW index scan returns at most ``ef_search`` rows, so any query whose
    #: LIMIT exceeds it comes back short with nothing to say so. pgvector's default
    #: is 40. Measured on 5,400 chunk vectors: at LIMIT 80 and ef_search 40 the
    #: search returned 40 rows and 50% of the exact top-80; at 100 and above it
    #: returned all of them. Callers must therefore keep ef_search at or above the
    #: number of rows they ask for, which is what ``_widen_hnsw_walk`` enforces —
    #: this value is the headroom above that floor.
    #:
    #: Raising it is cheap at this scale: 40, 100 and 200 all measured ~46ms/query
    #: against ~62ms for an exact scan, with 400 giving up the whole advantage.
    hnsw_ef_search: int = 200

    reranker_provider: str = "auto"  # auto | gemini | lexical

    # ── funnel widths (architecture doc §10) ──────────────────────────────
    retrieval_limit: int = 100
    rerank_limit: int = 20
    shortlist_size: int = 5

    weights: ScoringWeights = Field(default_factory=ScoringWeights)

    @field_validator("cors_origins", "allowed_upload_types", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def llm_available(self) -> bool:
        """True when Gemini can be reached; otherwise the offline engine is used."""
        if self.google_genai_use_vertexai:
            return bool(self.google_cloud_project)
        return bool(self.google_api_key)

    @property
    def cors_origin_regex(self) -> str | None:
        """Loopback-any-port pattern, or None when it must not apply."""
        if not self.cors_allow_loopback or self.environment != "development":
            return None
        return r"http://(localhost|127\.0\.0\.1)(:\d+)?"

    @property
    def mode(self) -> str:
        return "gemini" if self.llm_available else "offline"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    total = settings.weights.total()
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"Scoring weights must sum to 1.0, got {total}. Check WEIGHT_* env vars.")
    return settings


settings = get_settings()

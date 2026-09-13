# Recruitment API

FastAPI backend: candidate repository, screening funnel and explainable scoring.
Resumes are ingested once and matched against any future job description.

See the root [README](../README.md) for the architecture and the full run guide.

## Layout

```
app/
  main.py               app factory, middleware, error handlers
  api/
    dependencies.py     session + authenticated recruiter
    v1/
      router.py         aggregates the endpoint routers
      endpoints/        auth · resumes · candidates · jobs · matching · search ·
                        dashboard · health
  core/                 config, security, logging
  db/
    database.py         engine, session factory, Base
    models/             ORM tables — candidates, jobs, taxonomy, search sessions,
                        audit events
    repositories/       query objects — the hybrid SQL + pgvector retrieval lives here
  schemas/              wire contracts, one module per resource
  services/             ingestion, job creation, the funnel, matching, search
                        sessions, audit
  ai/
    embeddings/         hashing embedder (offline) + hosted provider
    llm/                Gemini client, requirement evaluator, explanation generator
    retrieval/          semantic retrieval stage
    ranking/            reranking (Gemini or lexical)
    classifier.py       multi-label role classification (§8)
    skill_normalizer.py canonical skill/industry dictionary (§8, §16.1)
    lexicon.py          deterministic skill/seniority vocabulary
    schemas.py          model-facing contracts, kept apart from the wire contracts
  parsers/              pdf · docx · resume · job description
  utils/                upload validation, text normalisation
  storage/              object storage (local / S3)
  workers/              Celery tasks + inline fallback
migrations/             alembic revisions
tests/
  unit/                 55 tests, no database and no credentials
  integration/          11 tests against real Postgres, skipped when unreachable
uploads/                local resume storage (gitignored)
requirements.txt        runtime dependencies
requirements-dev.txt    + pytest and ruff
pytest.ini · ruff.toml  tool configuration
Dockerfile
```

## Dependencies

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
```

- `requirements.txt` — runtime dependencies
- `requirements-dev.txt` — the above plus pytest and ruff (`-r requirements.txt`)

The app is **not** installed as a package. `app` is imported from the backend root,
so run every command from `backend/` (the Docker image sets `PYTHONPATH=/srv` for the
same reason). Tool config lives in `pytest.ini` and `ruff.toml`.

## Running

```bash
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe scripts/seed.py --screen
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

`GET /health` reports the live engine. Interactive docs at `/docs`.

## Tests and lint

```bash
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check .
```

## Container

```bash
docker build -t recruitment-api .
docker run --rm -p 8000:8000 --env-file .env recruitment-api
```

Mount a volume over `/srv/uploads` in any deployment where resume files must survive
a container replacement.

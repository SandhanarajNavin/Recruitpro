# Recruitment Agent

Upload resumes once into a persistent candidate repository, then match any job
description against it — returning the top five candidates with a transparent score
and evidence for both the match and the gaps.

Built to the V1 architecture in [`docs/architecture.html`](docs/architecture.html).

## The load-bearing idea

Candidate intelligence is built **independently of any job description**. A resume is
never parsed, filtered or embedded with respect to a JD. Ingestion and screening are
two separate workflows that meet at exactly one surface — the repository.

```
Candidate layer   resume → extract → parse → embed → repository        (once per resume)
Recruitment layer JD → requirements → retrieve → rerank → evaluate → score → explain
                                        ↑
                                   repository (read-only)
```

The consequence: a recruiter with a thousand resumes opens a new role by pasting a
description. Nothing is re-processed, and model cost stays flat as the pool grows.

## Layout

```
backend/              FastAPI backend — the whole pipeline lives here
  app/
    api/v1/endpoints/ auth · resumes · candidates · jobs · matching · dashboard · health
    core/             config, security, logging
    db/               engine, ORM tables, query repositories (incl. pgvector retrieval)
    schemas/          wire contracts, one module per resource
    services/         ingestion, job creation, the funnel, screening orchestration
    ai/               embeddings · llm (evaluator, explainer) · retrieval · ranking
    parsers/          pdf · docx · resume · job description
    utils/            upload validation, text normalisation
    workers/          Celery tasks + inline fallback
    storage/          object storage (local / S3)
  migrations/         alembic revisions
  tests/              unit (55) + integration (11)
  uploads/            local resume storage (gitignored)
  Dockerfile
frontend/             Next.js 15 App Router recruiter UI
docs/architecture.html
docker-compose.yml    Postgres 17 + pgvector, Redis
```

## Running it

### 1. Infrastructure

```bash
docker compose up -d
```

### 2. Backend

```bash
cd backend && python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements-dev.txt
```

```bash
cd backend && .venv/Scripts/python.exe -m alembic upgrade head
```

Seed a recruiter and ten sample candidates, then run one screening end to end:

```bash
cd backend && .venv/Scripts/python.exe scripts/seed.py --screen
```

Start the API:

```bash
cd backend && .venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

`GET /health` reports which engine is live. Interactive docs at
`http://localhost:8000/docs`.

### 3. Frontend

```bash
cd frontend && npm install && npm run dev
```

Open <http://localhost:3000> and sign in as `recruiter@example.com` / `recruiter123`.

### Optional: a Celery worker

Nothing is required to get started. Dispatch pings for a live worker before queueing
and runs the task inline when none answers, so uploads work either way — a resume can
never strand at "queued" because a worker was not running.

What the worker buys is bulk upload. Parsing and embedding one resume costs ~6s
against Vertex, and inline that is charged to the HTTP request: twenty files is a
two-minute upload that a proxy or browser will cut off. Queued, the same upload
returns in ~0.2s and the worker drains the backlog in the background, which the
existing per-file status polling already displays.

```bash
docker compose up -d worker
```

Measured on ten resumes, concurrency 4:

| | upload request | ingestion |
|---|---|---|
| inline (`TASK_ALWAYS_EAGER=true`) | ~61 s — blocks, times out | — |
| queued, worker running | **0.24 s** | 18 s in the background |

The worker mounts two things from the host: `backend/uploads`, because
`STORAGE_BACKEND=local` means the API writes a file the worker reads back by path,
and your gcloud config, so it can reach Vertex with your Application Default
Credentials. Point `GCLOUD_CONFIG_DIR` in the root `.env` at that directory
(`%APPDATA%/gcloud` on Windows, `~/.config/gcloud` elsewhere) and set
`WORKER_CONCURRENCY` to taste — ingestion waits on Vertex, so it can exceed cores.

To run it on the host instead of in Docker (Windows needs `--pool=solo`, which means
one resume at a time):

```bash
cd backend && .venv/Scripts/python.exe -m celery -A app.workers.celery_app.celery_app worker --loglevel=info --pool=solo
```

Set `TASK_ALWAYS_EAGER=true` to skip the queue entirely.

## It runs with no API key

Without credentials the system uses a deterministic engine end to end — lexicon
parsing, a local hashing embedder, lexical reranking, rules-based evaluation and
rule-derived explanations. Everything works; the shortlist is just less nuanced. The
mode is reported on `/health`, on the dashboard, and stored per screening so a result
is never ambiguous about which engine produced it.

Add a key to `backend/.env` to upgrade the parsing, evaluation and explanation
stages to Claude:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Each stage degrades independently: if the evaluator fails for one candidate, that
candidate falls back to rules, the rest keep their Claude scores, and the degradation
is recorded on the screening and shown in the UI.

## The funnel

Cost is bounded by shape, not by luck. Model calls only ever see `RERANK_LIMIT`
candidates, so a run against 100 and a run against 100,000 cost about the same.

| Stage | Engine | Width |
|---|---|---|
| Repository | Postgres | all active candidates |
| Hard filters | SQL gates | must-have skills, min years |
| Semantic retrieval | pgvector ANN (HNSW, cosine) | `RETRIEVAL_LIMIT` (100) |
| Rerank | cross-encoder or lexical | `RERANK_LIMIT` (20) |
| Requirement evaluation | LLM + deterministic rules | 20 — evidence, not a cut |
| Scoring + explanation | Python, then LLM prose | `SHORTLIST_SIZE` (5) |

Only importance-5 skills become hard gates: a gate is a candidate no score can
rescue. If the gates would empty the pool they are relaxed for that run and the
degradation is reported rather than silently returning nothing.

## Scoring is not the model's job

The LLM produces evidence and per-category scores. The composite is a weighted sum in
[`app/services/scoring.py`](backend/app/services/scoring.py), so the same evidence
and weights always produce the same number.

| Category | Weight |
|---|---|
| Required skills | 40% |
| Experience | 25% |
| Responsibilities / JD alignment | 20% |
| Education & certifications | 10% |
| Semantic similarity | 5% |

Weights live in configuration (`WEIGHT_*`) and must sum to 1.0 — the app refuses to
start otherwise. A category the evaluator did not return scores 0 rather than being
dropped: renormalising would reward a candidate for having less evidence.

Bands: ≥82 strong hire, ≥68 interview, ≥52 maybe, below that pass.

Two guardrails are structural rather than prompt-level. The scoring engine is the
only writer of the score, and the explanation generator is handed the evidence record
— never the resume text — so it has nothing to invent a qualification from.

## Tests

```bash
cd backend && .venv/Scripts/python.exe -m pytest
```

55 unit tests cover the deterministic core with no database and no credentials: date
arithmetic, lexicon boundaries, parsing, the funnel's ordering, the scoring
arithmetic, schema hardening and upload validation.

11 integration tests run against real Postgres and skip automatically when it is not
reachable. They cover what unit tests cannot — that the retrieval SQL is valid, that
ingestion persists a usable profile and vector, that re-parsing versions rather than
overwrites, that a screening never writes to the candidate layer, and that a
screening reproduces exactly on a second run.

## What is deliberately not here

| | Decision | Why |
|---|---|---|
| Dedicated vector DB | pgvector in Postgres | SQL filters compose with vector similarity in one query, one transaction, one backup |
| LangChain / LangGraph | Not used | V1 is a predictable workflow, not an autonomous agent |
| Kubernetes | Not used | Operational cost with no V1 benefit; the app stays modular so pieces can be extracted |
| Configurable weights UI | Config only | Per-job weights need a UI and an audit trail; ship the mechanism first |

## Caveats

Scores are decision support, not a decision. The pipeline reads only what is on the
resume, so it inherits whatever the resume over- or under-states — treat the
shortlist as a reading order for a human, and check the evidence strings before
acting on a rank. Recruiters can override any recommendation; the override is stored
alongside the computed score and never rewrites it.

Candidate identity resolution is limited to an exact email match. Fuzzy name matching
produces false merges, and merging two real people's histories is worse than holding
a duplicate.

# Deploying to Cloud Run

Two Cloud Run services (API and web), Cloud SQL for Postgres, a GCS bucket for resume
files, and Secret Manager. No Kubernetes, no Redis, and no long-lived service account
keys.

## Before you start

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

You need Owner or Editor on the project — the script creates service accounts and
grants IAM roles. Billing must be enabled; Cloud SQL will not create without it.

## Deploy

```bash
cd deploy/gcp
./deploy.sh all
```

Roughly 15-20 minutes, most of it Cloud SQL creating the instance. Phases can be run
one at a time and are safe to re-run — a phase that already succeeded is a no-op, so
a failure is resumed by running that phase again rather than by cleaning up first.

```bash
./deploy.sh apis      # enable the Google APIs
./deploy.sh infra     # Artifact Registry, Cloud SQL, bucket, secrets
./deploy.sh iam       # runtime service account and roles
./deploy.sh api       # build and deploy the backend
./deploy.sh migrate   # alembic upgrade head
./deploy.sh frontend  # build and deploy the web app
./deploy.sh cors      # let the web origin through
./deploy.sh urls      # print both service URLs
```

Override any default from the environment:

```bash
REGION=europe-west1 SQL_TIER=db-f1-micro ./deploy.sh all
```

When it finishes, open the web URL and create the first recruiter through the sign-up
page. There is no seeded admin account.

## Why the order matters

Two steps look like they could be merged and cannot:

**The frontend builds after the API.** `NEXT_PUBLIC_API_URL` is compiled into the
client bundle by `next build`, not read at runtime, so the API's URL has to exist
before the web image is built. A web image is therefore tied to one environment — a
staging build cannot be promoted to production.

**CORS is set after the frontend.** The web URL does not exist until the web service
is deployed, and that deploy needs the API URL. Something has to go second; widening
CORS afterwards is the cheaper half of the cycle to break.

**Migrations run as a Cloud Run job**, not from the container's entrypoint. Cloud Run
starts instances concurrently, and two of them running `alembic upgrade` against the
same database at once is how you corrupt a migration history.

## What gets created

| Resource | Default name | Notes |
|---|---|---|
| Cloud SQL | `recruitpro-db` | Postgres 17; migration 0001 creates the `vector` extension |
| GCS bucket | `PROJECT-recruitpro-resumes` | Uniform access, no public read path |
| Secrets | `recruitpro-{db-password,database-url,jwt-secret}` | Generated, never printed |
| Service account | `recruitpro-run` | `cloudsql.client`, `aiplatform.user`, `secretmanager.secretAccessor`, `storage.objectAdmin` on the bucket only |
| Cloud Run | `recruitpro-api`, `recruitpro-web` | Both public; the API authenticates with JWT |

Vertex AI is reached through the runtime service account, so **no key file is ever
downloaded**. This is the main reason Cloud Run is a better fit for this app than AWS,
where the same access needs a GCP key stored in a secrets manager.

## Storage must not be `local`

`STORAGE_BACKEND=gcs` is set by the deploy, and on Cloud Run it is not optional. Each
instance gets its own ephemeral filesystem, so a resume written by one instance is
invisible to the next and gone when the instance is recycled. `local` is a
development convenience.

## Ingestion runs inline

No Celery worker is deployed, so `TASK_ALWAYS_EAGER=true` and resume ingestion happens
inside the upload request — about 6 seconds per resume against Vertex. That is fine
for a handful of files and **will time out on twenty**: Cloud Run's request timeout is
set to 300s here, so roughly 40 resumes is the ceiling, and browsers and proxies give
up sooner.

The code needs no change to add a worker later. `enqueue_resume` probes for a live
Celery worker and only falls back to inline when none answers, so deploying a worker
and setting `TASK_ALWAYS_EAGER=false` is a configuration change. What it needs is a
Redis the worker and API can both reach — Memorystore (about $50/month) or Redis on a
small GCE VM with VPC access — plus a worker service with `--min-instances=1` and CPU
always allocated, since a scale-to-zero service cannot consume a queue.

## Costs

Dominated by Cloud SQL, which does not scale to zero. The default `db-custom-1-3840`
is roughly $50/month; `SQL_TIER=db-f1-micro` is a few dollars and is fine for
evaluation. Both Cloud Run services scale to zero and cost nothing idle. Vertex AI is
per token — parsing and embedding one resume is a fraction of a cent.

## Updating

```bash
./deploy.sh api        # redeploy the backend
./deploy.sh migrate    # after a new migration
./deploy.sh frontend   # redeploy the web app
```

Redeploy the frontend whenever the API URL changes, since it is baked into the bundle.

## Teardown

```bash
gcloud run services delete recruitpro-api recruitpro-web --region=us-central1
gcloud run jobs delete recruitpro-migrate --region=us-central1
gcloud sql instances delete recruitpro-db
gcloud storage rm -r gs://PROJECT-recruitpro-resumes
```

Deleting the bucket destroys every uploaded resume. The Cloud SQL instance holds the
parsed profiles and embeddings and is equally unrecoverable — take an export first if
the data matters.

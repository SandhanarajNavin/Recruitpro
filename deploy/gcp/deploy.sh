#!/usr/bin/env bash
#
# Deploy RecruitPro to Cloud Run.
#
# Run phases individually (./deploy.sh infra) or the lot (./deploy.sh all). Every
# phase is idempotent — re-running one that already succeeded is a no-op, so a failed
# run is resumed by re-running it rather than by unpicking what it did.
#
#   ./deploy.sh apis      enable the Google APIs this uses
#   ./deploy.sh infra     Artifact Registry, Cloud SQL, GCS bucket, secrets
#   ./deploy.sh iam       runtime service account and its roles
#   ./deploy.sh api       build and deploy the backend
#   ./deploy.sh migrate   run alembic against Cloud SQL
#   ./deploy.sh frontend  build and deploy the web app
#   ./deploy.sh cors      point the API's CORS at the deployed frontend
#   ./deploy.sh all       every phase above, in order
#
set -euo pipefail

# ── configuration ─────────────────────────────────────────────────────────────
# Override any of these in the environment: PROJECT_ID=my-proj ./deploy.sh all
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"

SQL_INSTANCE="${SQL_INSTANCE:-recruitpro-db}"
SQL_TIER="${SQL_TIER:-db-custom-1-3840}"
SQL_DATABASE="${SQL_DATABASE:-recruitment}"
SQL_USER="${SQL_USER:-recruitment}"

BUCKET="${BUCKET:-${PROJECT_ID}-recruitpro-resumes}"
REPO="${REPO:-recruitpro}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-recruitpro-run}"
API_SERVICE="${API_SERVICE:-recruitpro-api}"
WEB_SERVICE="${WEB_SERVICE:-recruitpro-web}"

# Where this script lives, so it can be run from anywhere.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"

IMAGE_HOST="${REGION}-docker.pkg.dev"
IMAGE_BASE="${IMAGE_HOST}/${PROJECT_ID}/${REPO}"
SA_EMAIL="${SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com"
SQL_CONNECTION="${PROJECT_ID}:${REGION}:${SQL_INSTANCE}"

say() { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }
note() { printf "    %s\n" "$*"; }
die() { printf "\n\033[1;31mERROR: %s\033[0m\n" "$*" >&2; exit 1; }

[[ -n "${PROJECT_ID}" ]] || die "No project. Set PROJECT_ID or run: gcloud config set project <id>"

# Idempotence helper: run a create command, tolerate "already exists".
create_ok() {
  local what="$1"; shift
  if "$@" 2>/tmp/deploy_err; then
    note "created ${what}"
  elif grep -qiE "already exists|alreadyExists|duplicate" /tmp/deploy_err; then
    note "${what} already exists"
  else
    cat /tmp/deploy_err >&2
    die "failed creating ${what}"
  fi
}

# ── phases ────────────────────────────────────────────────────────────────────

phase_apis() {
  say "Enabling APIs"
  gcloud services enable \
    run.googleapis.com \
    sqladmin.googleapis.com \
    secretmanager.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    aiplatform.googleapis.com \
    storage.googleapis.com \
    --project "${PROJECT_ID}"
  note "done"
}

phase_infra() {
  say "Artifact Registry"
  create_ok "repo ${REPO}" gcloud artifacts repositories create "${REPO}" \
    --repository-format=docker --location="${REGION}" --project="${PROJECT_ID}" \
    --description="RecruitPro images"

  say "Cloud SQL (Postgres 17 + pgvector)"
  note "this takes several minutes on first create"
  # --edition is not optional here. Postgres 17 defaults to ENTERPRISE_PLUS, which
  # rejects every db-custom-* tier and only accepts db-perf-optimized-N-*, so the
  # create fails on tier validation before it starts. ENTERPRISE is also the cheaper
  # edition and the one db-f1-micro and db-g1-small exist on.
  create_ok "instance ${SQL_INSTANCE}" gcloud sql instances create "${SQL_INSTANCE}" \
    --database-version=POSTGRES_17 --edition=ENTERPRISE --tier="${SQL_TIER}" \
    --region="${REGION}" --storage-auto-increase --project="${PROJECT_ID}"

  create_ok "database ${SQL_DATABASE}" gcloud sql databases create "${SQL_DATABASE}" \
    --instance="${SQL_INSTANCE}" --project="${PROJECT_ID}"

  # Generated once and kept only in Secret Manager — it is never echoed, and the
  # DATABASE_URL secret below is the only thing that needs to know it.
  if ! gcloud secrets describe recruitpro-db-password --project="${PROJECT_ID}" >/dev/null 2>&1; then
    local db_password
    db_password="$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)"
    printf '%s' "${db_password}" | gcloud secrets create recruitpro-db-password \
      --data-file=- --replication-policy=automatic --project="${PROJECT_ID}"
    gcloud sql users create "${SQL_USER}" --instance="${SQL_INSTANCE}" \
      --password="${db_password}" --project="${PROJECT_ID}" >/dev/null
    note "created SQL user and stored its password"

    # psycopg reaches Cloud Run's Cloud SQL socket through the host= query
    # parameter; there is no TCP host in this URL at all.
    printf '%s' "postgresql+psycopg://${SQL_USER}:${db_password}@/${SQL_DATABASE}?host=/cloudsql/${SQL_CONNECTION}" \
      | gcloud secrets create recruitpro-database-url \
        --data-file=- --replication-policy=automatic --project="${PROJECT_ID}"
    note "stored DATABASE_URL"
  else
    note "database password and URL secrets already exist"
  fi

  say "JWT secret"
  if ! gcloud secrets describe recruitpro-jwt-secret --project="${PROJECT_ID}" >/dev/null 2>&1; then
    openssl rand -base64 48 | tr -d '\n' | gcloud secrets create recruitpro-jwt-secret \
      --data-file=- --replication-policy=automatic --project="${PROJECT_ID}"
    note "generated"
  else
    note "already exists"
  fi

  say "GCS bucket for resume files"
  # Uniform access and no public path: the API streams bytes after authorising,
  # so nothing here should ever be world-readable.
  create_ok "bucket ${BUCKET}" gcloud storage buckets create "gs://${BUCKET}" \
    --location="${REGION}" --uniform-bucket-level-access --project="${PROJECT_ID}"
}

phase_iam() {
  say "Runtime service account"
  create_ok "service account ${SERVICE_ACCOUNT}" gcloud iam service-accounts create "${SERVICE_ACCOUNT}" \
    --display-name="RecruitPro Cloud Run runtime" --project="${PROJECT_ID}"

  note "granting roles"
  for role in roles/cloudsql.client roles/aiplatform.user roles/secretmanager.secretAccessor; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${SA_EMAIL}" --role="${role}" \
      --condition=None --quiet >/dev/null
    note "  ${role}"
  done

  # Scoped to the one bucket rather than project-wide storage admin.
  gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
    --member="serviceAccount:${SA_EMAIL}" --role=roles/storage.objectAdmin \
    --project="${PROJECT_ID}" --quiet >/dev/null
  note "  roles/storage.objectAdmin on gs://${BUCKET}"
  note "no keys are downloaded: Cloud Run gets these through Workload Identity"

  # Cloud Build runs as the Compute Engine default service account, and on projects
  # created since roughly 2024 that account is no longer granted the build roles
  # automatically. Without these the very first `builds submit` fails reading back
  # the source tarball it just uploaded — a 403 on storage.objects.get that reads
  # like a bucket problem and is really a missing role.
  say "Cloud Build service account"
  local project_number cloudbuild_sa
  project_number="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
  cloudbuild_sa="${project_number}-compute@developer.gserviceaccount.com"
  for role in roles/cloudbuild.builds.builder roles/logging.logWriter roles/artifactregistry.writer; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${cloudbuild_sa}" --role="${role}" \
      --condition=None --quiet >/dev/null
    note "  ${role}"
  done
}

phase_api() {
  # Carry the existing CORS origin into this deploy.
  #
  # --set-env-vars below replaces the whole environment rather than merging, so
  # without this every API deploy silently dropped the CORS_ORIGINS that phase_cors
  # had added. The API stayed healthy and the browser stopped being able to call it:
  # cors_origins fell back to its localhost default, every request from the deployed
  # web app failed preflight, and the frontend reported it as "Cannot reach the API"
  # — which reads like the backend is down when it is answering fine.
  # Read back from the env list rather than a --format filter: gcloud's filter()
  # transform rejects the two-argument form this needs and crashes outright.
  local existing_cors
  existing_cors="$(gcloud run services describe "${API_SERVICE}" --region="${REGION}" \
    --project="${PROJECT_ID}" \
    --format='value(spec.template.spec.containers[0].env)' 2>/dev/null \
    | tr ';' '\n' | grep "'CORS_ORIGINS'" \
    | sed -E "s/.*'value': *'([^']*)'.*/\1/" || true)"
  if [[ -z "${existing_cors}" ]]; then
    # First deploy, or the web app is not up yet: fall back to whatever is deployed,
    # and leave it empty if nothing is. phase_cors sets it once the web app exists.
    existing_cors="$(web_url 2>/dev/null || true)"
  fi

  say "Building the API image"
  gcloud builds submit "${ROOT}/backend" \
    --tag "${IMAGE_BASE}/api:latest" --project="${PROJECT_ID}" --region="${REGION}"

  say "Deploying ${API_SERVICE}"
  gcloud run deploy "${API_SERVICE}" \
    --image "${IMAGE_BASE}/api:latest" \
    --region "${REGION}" \
    --project "${PROJECT_ID}" \
    --service-account "${SA_EMAIL}" \
    --add-cloudsql-instances "${SQL_CONNECTION}" \
    --allow-unauthenticated \
    --memory 2Gi \
    --cpu 2 \
    --timeout 300 \
    --set-env-vars "ENVIRONMENT=production,API_RELOAD=false,CORS_ALLOW_LOOPBACK=false" \
    --set-env-vars "STORAGE_BACKEND=gcs,GCS_BUCKET=${BUCKET}" \
    --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION}" \
    --set-env-vars "EMBEDDING_PROVIDER=gemini" \
    --set-env-vars "TASK_ALWAYS_EAGER=true" \
    --set-env-vars "CORS_ORIGINS=${existing_cors}" \
    --set-secrets "DATABASE_URL=recruitpro-database-url:latest,JWT_SECRET=recruitpro-jwt-secret:latest"

  if [[ -n "${existing_cors}" ]]; then
    note "kept CORS_ORIGINS=${existing_cors}"
  else
    note "CORS_ORIGINS is empty — run ./deploy.sh cors once the web app is deployed"
  fi
  note "API: $(api_url)"
  note "TASK_ALWAYS_EAGER=true because no worker is deployed — ingestion runs in the"
  note "upload request at roughly 6s per resume. See the README before bulk uploading."
}

phase_migrate() {
  say "Running migrations"
  # A job rather than a container entrypoint: two Cloud Run instances starting at
  # once would otherwise both run alembic against the same database.
  if gcloud run jobs describe recruitpro-migrate --region="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud run jobs update recruitpro-migrate \
      --image "${IMAGE_BASE}/api:latest" --region "${REGION}" --project "${PROJECT_ID}" \
      --service-account "${SA_EMAIL}" --set-cloudsql-instances "${SQL_CONNECTION}" \
      --set-secrets "DATABASE_URL=recruitpro-database-url:latest" \
      --command=python --args=-m,alembic,upgrade,head >/dev/null
  else
    gcloud run jobs create recruitpro-migrate \
      --image "${IMAGE_BASE}/api:latest" --region "${REGION}" --project "${PROJECT_ID}" \
      --service-account "${SA_EMAIL}" --set-cloudsql-instances "${SQL_CONNECTION}" \
      --set-secrets "DATABASE_URL=recruitpro-database-url:latest" \
      --command=python --args=-m,alembic,upgrade,head >/dev/null
  fi
  gcloud run jobs execute recruitpro-migrate --region="${REGION}" --project="${PROJECT_ID}" --wait
  note "schema is at head (this also creates the pgvector extension)"
}

api_url() {
  gcloud run services describe "${API_SERVICE}" --region="${REGION}" \
    --project="${PROJECT_ID}" --format='value(status.url)'
}

web_url() {
  gcloud run services describe "${WEB_SERVICE}" --region="${REGION}" \
    --project="${PROJECT_ID}" --format='value(status.url)'
}

phase_frontend() {
  local api
  api="$(api_url)"
  [[ -n "${api}" ]] || die "The API is not deployed yet — run: ./deploy.sh api"

  say "Building the web image against ${api}"
  # NEXT_PUBLIC_API_URL is compiled into the client bundle, so the API has to exist
  # before this builds. That is why the frontend cannot be deployed first, and why a
  # web image is tied to one environment.
  gcloud builds submit "${ROOT}/frontend" \
    --project="${PROJECT_ID}" --region="${REGION}" \
    --substitutions="_API_URL=${api},_IMAGE=${IMAGE_BASE}/web:latest" \
    --config="${HERE}/cloudbuild.web.yaml"

  say "Deploying ${WEB_SERVICE}"
  gcloud run deploy "${WEB_SERVICE}" \
    --image "${IMAGE_BASE}/web:latest" \
    --region "${REGION}" --project "${PROJECT_ID}" \
    --allow-unauthenticated --memory 512Mi --cpu 1
  note "Web: $(web_url)"
}

phase_cors() {
  local web
  web="$(web_url)"
  [[ -n "${web}" ]] || die "The frontend is not deployed yet — run: ./deploy.sh frontend"

  say "Allowing ${web} through CORS"
  # Last, not with the API deploy: the frontend's URL does not exist until it has
  # been deployed, and it is deployed against the API's URL. Something has to go
  # second, and widening CORS afterwards is the cheaper half of the cycle to break.
  gcloud run services update "${API_SERVICE}" \
    --region "${REGION}" --project "${PROJECT_ID}" \
    --update-env-vars "CORS_ORIGINS=${web}"
  note "done"
}

phase_all() {
  phase_apis; phase_infra; phase_iam; phase_api; phase_migrate; phase_frontend; phase_cors
  say "Deployed"
  note "Web: $(web_url)"
  note "API: $(api_url)"
  note "Create the first recruiter through the web app's sign-up page."
}

case "${1:-}" in
  apis) phase_apis ;;
  infra) phase_infra ;;
  iam) phase_iam ;;
  api) phase_api ;;
  migrate) phase_migrate ;;
  frontend) phase_frontend ;;
  cors) phase_cors ;;
  all) phase_all ;;
  urls) echo "api=$(api_url)"; echo "web=$(web_url)" ;;
  *)
    sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
    exit 1
    ;;
esac

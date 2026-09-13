# syntax=docker/dockerfile:1

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/srv

WORKDIR /srv

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Dependencies first, in their own layer: psycopg[binary] ships its own libpq, so
# there is no build toolchain to install. This layer is cached until
# requirements.txt changes — editing application code does not trigger a reinstall.
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY alembic.ini pytest.ini ./
COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts

# The app is imported from /srv (see PYTHONPATH) rather than installed as a package.
# Resume files land in uploads/ when STORAGE_BACKEND=local; mount a volume over it
# in any deployment that must survive a container replacement.
RUN mkdir -p uploads \
 && useradd --create-home --uid 10001 appuser \
 && chown -R appuser:appuser /srv
VOLUME ["/srv/uploads"]
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

"""Celery application (architecture doc §17).

``task_always_eager`` lets the whole system run with no Redis and no worker process —
tasks execute inline in the caller. That is the difference between "clone and run"
and "install two services first", and it costs one setting.
"""

from __future__ import annotations

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "recruitment",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    # Ingestion and screening are idempotent, so late acknowledgement is safe and
    # means a worker crash re-runs the task instead of losing it.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_always_eager=settings.task_always_eager,
    task_eager_propagates=False,
    broker_connection_retry_on_startup=True,
)

"""Enqueue helpers.

Wrapping ``.delay()`` means a broker outage degrades to inline execution with a
warning rather than a 500 on upload. The API contract is unchanged either way: the
caller gets an id back and polls for status.

A broker outage is the *easy* failure. The dangerous one is a healthy broker with no
worker listening: ``.delay()`` succeeds, the task sits in Redis, and the resume stays
"queued" forever while the UI polls it. Nothing raises, so there is nothing to catch.
``_workers_available`` is what turns that silent hang back into the inline fallback.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: How long a worker probe is trusted before being repeated. A probe is a broker
#: round trip, and a bulk upload dispatches once per file — without this, dropping
#: in twenty resumes would ping the broker twenty times.
_PROBE_TTL_S = 10.0

#: Ping timeout. Paid in full only when no worker answers, which is exactly the case
#: this is here to detect, so it has to stay small enough to not stall an upload.
_PROBE_TIMEOUT_S = 0.75

#: (probed_at, available) from the last ping, or None before the first.
_probe: tuple[float, bool] | None = None


def _workers_available() -> bool:
    """True when at least one Celery worker answers a ping.

    Eager mode short-circuits: ``.delay()`` runs the task in the caller there, so the
    answer is always yes and a probe would be a round trip for nothing.
    """
    if settings.task_always_eager:
        return True

    global _probe
    now = time.monotonic()
    if _probe is not None and now - _probe[0] < _PROBE_TTL_S:
        return _probe[1]

    try:
        from app.workers.celery_app import celery_app

        # limit=1 returns on the first reply instead of waiting out the timeout to
        # collect every worker's. One answer is the whole question here, and without
        # it the healthy case pays the full timeout it is meant to avoid.
        available = bool(
            celery_app.control.ping(timeout=_PROBE_TIMEOUT_S, limit=1)
        )
    except Exception as exc:  # noqa: BLE001 - an unreachable broker is "no workers"
        logger.warning("Could not probe for Celery workers: %s", exc)
        available = False

    _probe = (now, available)
    return available


def reset_worker_probe() -> None:
    """Forget the cached probe. For tests, and for anything that needs the next
    dispatch to re-check rather than wait out the TTL."""
    global _probe
    _probe = None


def _dispatch(task, task_id: uuid.UUID, run_inline: Callable[[], None], label: str) -> str:
    """Queue ``task`` if a worker will take it, otherwise run ``run_inline`` here."""
    if not _workers_available():
        logger.warning(
            "No Celery worker is listening; running %s inline. Start a worker "
            "(docker compose up worker) to process these in the background.",
            label,
        )
        run_inline()
        return "inline"

    try:
        result = task.delay(str(task_id))
        return getattr(result, "id", "eager")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Queue unavailable, running %s inline: %s", label, exc)
        run_inline()
        return "inline"


def enqueue_resume(resume_id: uuid.UUID) -> str:
    from app.workers.tasks import process_resume_task

    def run_inline() -> None:
        from app.db.database import session_scope
        from app.services import resume_service

        session = session_scope()
        try:
            resume_service.process_resume(session, resume_id)
        finally:
            session.close()

    return _dispatch(process_resume_task, resume_id, run_inline, "resume ingestion")


def enqueue_screening(screening_id: uuid.UUID) -> str:
    from app.workers.tasks import run_screening_task

    def run_inline() -> None:
        from app.db.database import session_scope
        from app.services import matching_service

        session = session_scope()
        try:
            matching_service.run_screening(session, screening_id)
        finally:
            session.close()

    return _dispatch(run_screening_task, screening_id, run_inline, "screening")

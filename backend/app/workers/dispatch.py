"""Enqueue helpers.

Wrapping ``.delay()`` means a broker outage degrades to inline execution with a
warning rather than a 500 on upload. The API contract is unchanged either way: the
caller gets an id back and polls for status.
"""

from __future__ import annotations

import uuid

from app.core.logging import get_logger

logger = get_logger(__name__)


def enqueue_resume(resume_id: uuid.UUID) -> str:
    from app.workers.tasks import process_resume_task

    try:
        result = process_resume_task.delay(str(resume_id))
        return getattr(result, "id", "eager")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Queue unavailable, processing resume inline: %s", exc)
        from app.db.database import session_scope
        from app.services import resume_service

        session = session_scope()
        try:
            resume_service.process_resume(session, resume_id)
        finally:
            session.close()
        return "inline"


def enqueue_screening(screening_id: uuid.UUID) -> str:
    from app.workers.tasks import run_screening_task

    try:
        result = run_screening_task.delay(str(screening_id))
        return getattr(result, "id", "eager")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Queue unavailable, running screening inline: %s", exc)
        from app.db.database import session_scope
        from app.services import matching_service

        session = session_scope()
        try:
            matching_service.run_screening(session, screening_id)
        finally:
            session.close()
        return "inline"

"""Background tasks. Each owns its own session — a worker must never borrow the
request's session."""

from __future__ import annotations

import uuid

from app.core.logging import get_logger
from app.db.database import session_scope
from app.services import matching_service, resume_service
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="resume.process", bind=True, max_retries=3, default_retry_delay=30)
def process_resume_task(self, resume_id: str) -> None:  # noqa: ANN001
    session = session_scope()
    try:
        resume_service.process_resume(session, uuid.UUID(resume_id))
    finally:
        session.close()


@celery_app.task(name="screening.run", bind=True, max_retries=1, default_retry_delay=30)
def run_screening_task(self, screening_id: str) -> None:  # noqa: ANN001
    session = session_scope()
    try:
        matching_service.run_screening(session, uuid.UUID(screening_id))
    finally:
        session.close()

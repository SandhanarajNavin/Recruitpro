"""Persisted audit trail (architecture doc §19).

``core.logging.audit`` writes a line to the log sink; this writes a row. Both exist
because they answer different questions: logs tell you what happened this week, the
table tells you who accessed a given candidate eight months ago, which is the
question a data-protection request actually asks.

Writing an audit row must never break the request it is recording. Every failure
here is swallowed and logged — a storage hiccup should not turn a successful resume
download into a 500.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.logging import audit as audit_log
from app.core.logging import get_logger
from app.db.models import AuditEvent

logger = get_logger(__name__)


def record(
    session: Session,
    action: str,
    *,
    recruiter_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    request_id: str | None = None,
    **detail: object,
) -> None:
    """Log the event and persist it. Never raises.

    ``detail`` must stay free of PII (§20) — counts, ids and outcomes only.
    """
    audit_log(
        action,
        recruiter_id=recruiter_id,
        resource_type=resource_type,
        resource_id=resource_id,
        **detail,
    )
    try:
        session.add(
            AuditEvent(
                recruiter_id=recruiter_id,
                action=action[:48],
                resource_type=resource_type,
                resource_id=resource_id,
                request_id=request_id,
                detail={key: str(value) for key, value in detail.items()},
            )
        )
        session.commit()
    except Exception:  # noqa: BLE001 - auditing must not break the audited action
        session.rollback()
        logger.exception("Failed to persist audit event %s", action)


def record_denied(
    session: Session,
    *,
    recruiter_id: uuid.UUID | None,
    resource_type: str,
    resource_id: uuid.UUID | None = None,
    reason: str = "not_owner",
) -> None:
    """An attempted cross-tenant access is the event most worth keeping (§19)."""
    record(
        session,
        "access.denied",
        recruiter_id=recruiter_id,
        resource_type=resource_type,
        resource_id=resource_id,
        reason=reason,
    )

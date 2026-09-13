"""Liveness.

Mounted at the application root rather than under the versioned prefix: probes and
operators expect ``/health`` to be stable across API versions.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter(tags=["meta"])


@router.get("/health")
def health() -> dict[str, object]:
    """Liveness plus the two facts an operator actually needs to see."""
    return {
        "status": "ok",
        "mode": settings.mode,
        "embedding_model": settings.embedding_model,
    }

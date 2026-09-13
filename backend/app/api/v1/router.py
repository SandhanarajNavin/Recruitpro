"""v1 router aggregation.

``health`` is deliberately absent: it is mounted at the application root in
``app.main`` so the liveness URL stays ``/health`` rather than moving under the
versioned prefix.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    applications,
    auth,
    candidates,
    chat,
    dashboard,
    interviews,
    jobs,
    matching,
    resumes,
    search,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(resumes.router)
api_router.include_router(candidates.router)
api_router.include_router(jobs.router)
api_router.include_router(matching.router)
api_router.include_router(search.router)
api_router.include_router(dashboard.router)
api_router.include_router(applications.router)
api_router.include_router(interviews.router)
api_router.include_router(chat.router)

__all__ = ["api_router"]

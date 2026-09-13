"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.api.v1.endpoints import health
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info(
        "%s starting — mode=%s embedding=%s eager_tasks=%s",
        settings.app_name,
        settings.mode,
        settings.embedding_model,
        settings.task_always_eager,
    )
    if not settings.llm_available:
        logger.info(
            "No Gemini credentials found. Running the deterministic engine — parsing, "
            "retrieval, scoring and explanations all work, without model calls."
        )
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Candidate repository, screening funnel and explainable scoring. "
        "Resumes are ingested once and matched against any future job description."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # Development only — see Settings.cors_origin_regex. Returns None in any other
    # environment, so production is the explicit allowlist and nothing more.
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_v1_prefix)
app.include_router(health.router)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """A domain ValueError is a client mistake, not a server fault."""
    logger.info("Rejected request to %s: %s", request.url.path, exc)
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(OperationalError)
async def database_unavailable_handler(request: Request, exc: OperationalError) -> JSONResponse:
    """An unreachable database is a 503, not a 500, and deserves a real message.

    Without this the most common first-run mistake — starting the API before
    `docker compose up` — surfaces as a bare "Internal Server Error" with nothing to
    act on.
    """
    logger.error("Database unavailable for %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=503,
        content={
            "detail": (
                "The database is unavailable. Start it with `docker compose up -d` and "
                "apply migrations with `alembic upgrade head`."
            )
        },
    )


def run() -> None:
    """Entrypoint for ``python -m app.main``.

    A convenience for local work only. The import string is passed rather than the
    app object because uvicorn's reloader has to re-import the module in the child
    process; handing it the object disables reload silently.
    """
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload,
    )


if __name__ == "__main__":
    run()

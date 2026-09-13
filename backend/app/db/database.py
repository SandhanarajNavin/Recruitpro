"""Engine, session factory and the FastAPI session dependency.

Sync SQLAlchemy on purpose: Celery workers are synchronous, and sharing one session
style between the API and the workers removes a whole class of bug. FastAPI runs
`def` endpoints in a threadpool, which is the right trade at this scale.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    # Without an explicit timeout psycopg waits indefinitely for a TCP connection,
    # so a database that is down turns every request into a hung spinner rather than
    # a fast, legible error. Only applies to Postgres URLs.
    connect_args=(
        {"connect_timeout": settings.db_connect_timeout}
        if settings.database_url.startswith("postgresql")
        else {}
    ),
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def session_scope() -> Session:
    """A bare session for workers and scripts. Caller owns commit/close."""
    return SessionLocal()

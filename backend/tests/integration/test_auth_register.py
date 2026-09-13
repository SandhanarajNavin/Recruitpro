"""Recruiter self-signup (architecture doc §6).

Integration rather than unit tests because the things worth proving are properties of
stored rows: email uniqueness, the role the database actually ends up with, and that
a brand-new recruiter starts with an empty, isolated repository.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text

from app.core.config import settings
from app.db.models import User
from app.db.models.user import UserRole


def _database_available() -> bool:
    try:
        engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 2},
        )
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            connection.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).one()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _database_available(),
    reason=(
        "Postgres with pgvector is not reachable — run "
        "`docker compose up -d && alembic upgrade head`"
    ),
)


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


@pytest.fixture
def session():
    from app.db.database import session_scope

    db = session_scope()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@pytest.fixture
def cleanup(session):
    """Remove any user the test created, whatever the assertions did."""
    created: list[str] = []
    yield created
    for email in created:
        user = session.execute(select(User).where(User.email == email)).scalars().first()
        if user is not None:
            session.delete(user)
    session.commit()


def _email() -> str:
    return f"signup-{uuid.uuid4().hex[:12]}@example.com"


class TestRegister:
    def test_creates_an_account_and_returns_a_usable_token(self, client, cleanup):
        email = _email()
        cleanup.append(email)

        response = client.post(
            "/api/v1/auth/register",
            json={"name": "New Recruiter", "email": email, "password": "a-good-password"},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["user"]["email"] == email
        assert body["token_type"] == "bearer"

        # The token works immediately — signup signs you in.
        me = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {body['access_token']}"},
        )
        assert me.status_code == 200
        assert me.json()["email"] == email

    def test_new_account_is_a_recruiter_never_an_admin(self, client, cleanup, session):
        """Role must not be settable from the request body (doc §16.2)."""
        email = _email()
        cleanup.append(email)

        response = client.post(
            "/api/v1/auth/register",
            json={
                "name": "Sneaky",
                "email": email,
                "password": "a-good-password",
                "role": "admin",  # ignored
            },
        )
        assert response.status_code == 201
        assert response.json()["user"]["role"] == UserRole.RECRUITER.value

        stored = session.execute(select(User).where(User.email == email)).scalars().one()
        assert stored.role == UserRole.RECRUITER.value

    def test_email_is_normalised_and_can_then_log_in(self, client, cleanup):
        email = _email()
        cleanup.append(email)

        created = client.post(
            "/api/v1/auth/register",
            json={"name": "Case Test", "email": email.upper(), "password": "a-good-password"},
        )
        assert created.status_code == 201
        assert created.json()["user"]["email"] == email  # stored lowercase

        # Login lowercases too, so either casing gets in.
        signed_in = client.post(
            "/api/v1/auth/login", json={"email": email.upper(), "password": "a-good-password"}
        )
        assert signed_in.status_code == 200

    def test_duplicate_email_is_rejected(self, client, cleanup):
        email = _email()
        cleanup.append(email)

        first = client.post(
            "/api/v1/auth/register",
            json={"name": "First", "email": email, "password": "a-good-password"},
        )
        assert first.status_code == 201

        second = client.post(
            "/api/v1/auth/register",
            json={"name": "Second", "email": email, "password": "another-password"},
        )
        assert second.status_code == 409
        assert "already registered" in second.json()["detail"].lower()

    def test_new_recruiter_sees_an_empty_repository(self, client, cleanup):
        """A fresh tenant must not inherit anyone else's candidates (doc §6)."""
        email = _email()
        cleanup.append(email)

        token = client.post(
            "/api/v1/auth/register",
            json={"name": "Isolated", "email": email, "password": "a-good-password"},
        ).json()["access_token"]

        listing = client.get(
            "/api/v1/candidates", headers={"Authorization": f"Bearer {token}"}
        )
        assert listing.status_code == 200
        assert listing.json()["total"] == 0

    @pytest.mark.parametrize(
        ("payload", "field"),
        [
            ({"name": "A", "email": "x@example.com", "password": "a-good-password"}, "name"),
            ({"name": "Valid", "email": "not-an-email", "password": "a-good-password"}, "email"),
            ({"name": "Valid", "email": "x@example.com", "password": "short"}, "password"),
        ],
    )
    def test_invalid_input_is_rejected(self, client, payload, field):
        response = client.post("/api/v1/auth/register", json=payload)
        assert response.status_code == 422, f"{field} should have been rejected"

    def test_password_beyond_bcrypt_limit_is_rejected_not_truncated(self, client):
        """bcrypt ignores past 72 bytes; silently truncating would be a security
        surprise, so the API refuses instead."""
        response = client.post(
            "/api/v1/auth/register",
            json={"name": "Long Pass", "email": _email(), "password": "x" * 100},
        )
        assert response.status_code == 422

    def test_signup_can_be_disabled(self, client, monkeypatch):
        monkeypatch.setattr(settings, "allow_self_signup", False)
        response = client.post(
            "/api/v1/auth/register",
            json={"name": "Blocked", "email": _email(), "password": "a-good-password"},
        )
        assert response.status_code == 403

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import current_user
from app.core.config import settings
from app.core.logging import audit
from app.core.security import create_access_token, hash_password, verify_password
from app.db.database import get_session
from app.db.models import User
from app.db.models.user import UserRole
from app.schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, session: Session = Depends(get_session)) -> TokenResponse:
    """Create a recruiter account and sign them straight in.

    The new account is its own tenant: every candidate, job and search it creates is
    scoped to this user id (doc §6). The role is hardcoded to recruiter — accepting
    one from the body would let a caller mint an admin.
    """
    if not settings.allow_self_signup:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Self-service signup is disabled. Ask an administrator for an account.",
        )

    email = body.email.lower().strip()
    existing = session.execute(select(User).where(User.email == email)).scalars().first()
    if existing is not None:
        # Unlike login, this necessarily confirms the address is registered — an
        # email column has to be unique and the user needs to know why it failed.
        audit("auth.register_conflict", email=email)
        raise HTTPException(
            status.HTTP_409_CONFLICT, "That email is already registered. Sign in instead."
        )

    user = User(
        name=body.name,
        email=email,
        password_hash=hash_password(body.password),
        role=UserRole.RECRUITER.value,
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    audit("auth.registered", user_id=user.id)
    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role),
        user=UserOut.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, session: Session = Depends(get_session)) -> TokenResponse:
    user = session.execute(
        select(User).where(User.email == body.email.lower())
    ).scalars().first()

    # Same message and same work for both failure modes, so the response does not
    # reveal whether an address is registered.
    if user is None or not verify_password(body.password, user.password_hash):
        audit("auth.login_failed", email=body.email)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled.")

    audit("auth.login", user_id=user.id)
    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role),
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> UserOut:
    return UserOut.model_validate(user)

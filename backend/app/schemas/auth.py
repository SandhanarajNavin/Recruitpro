"""Login and identity contracts."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.common import ORMModel


class UserOut(ORMModel):
    id: uuid.UUID
    name: str
    email: str
    role: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


#: bcrypt hashes at most 72 bytes and silently ignores the rest, so anything longer
#: would make the tail of a passphrase decorative. Rejecting is honest; truncating is
#: a security surprise.
MAX_PASSWORD_BYTES = 72


class RegisterRequest(BaseModel):
    """Self-service recruiter signup.

    A new account is always a recruiter — role is never taken from the request, or
    an attacker would simply ask for "admin" (doc §16.2).
    """

    name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)

    @field_validator("password")
    @classmethod
    def _fits_bcrypt(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(
                f"Password must be at most {MAX_PASSWORD_BYTES} bytes "
                "(bcrypt ignores anything beyond that)."
            )
        return value

    @field_validator("name")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name cannot be blank.")
        return cleaned

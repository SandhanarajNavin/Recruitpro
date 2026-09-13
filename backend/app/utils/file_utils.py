"""Upload validation and filename handling.

Validation lives here rather than in the ingestion service because it runs before
anything is stored — it is a property of the bytes, not of the pipeline.
"""

from __future__ import annotations

import hashlib

from app.core.config import settings

ALLOWED_SUFFIXES = {"pdf", "docx", "doc", "txt", "md"}


class UploadRejected(ValueError):
    """The file failed validation and was never stored."""


def suffix_of(filename: str) -> str:
    return filename.lower().rsplit(".", 1)[-1] if "." in filename else ""


def sha256_checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def provisional_name(filename: str) -> str:
    """A stand-in candidate name until the parser supplies the real one."""
    stem = filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip()
    return stem[:200] or "Unnamed"


def validate_upload(filename: str, content_type: str, data: bytes) -> None:
    if not data:
        raise UploadRejected("The uploaded file is empty.")
    if len(data) > settings.max_upload_bytes:
        limit_mb = settings.max_upload_bytes / (1024 * 1024)
        raise UploadRejected(f"File exceeds the {limit_mb:.0f} MB limit.")

    suffix = suffix_of(filename)
    if content_type not in settings.allowed_upload_types and suffix not in ALLOWED_SUFFIXES:
        raise UploadRejected(f"Unsupported file type: {content_type or suffix or 'unknown'}.")

    # Cheap magic-byte sanity check: a content-type header is caller-supplied and a
    # mismatched one is the classic way to smuggle a payload past a type allowlist.
    if suffix == "pdf" and not data.startswith(b"%PDF"):
        raise UploadRejected("File claims to be a PDF but does not start with %PDF.")
    if suffix == "docx" and not data.startswith(b"PK"):
        raise UploadRejected("File claims to be a DOCX but is not a zip container.")

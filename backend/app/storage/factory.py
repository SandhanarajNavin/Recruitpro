from __future__ import annotations

from app.core.config import settings
from app.storage.base import ObjectStorage

_storage: ObjectStorage | None = None


def get_storage() -> ObjectStorage:
    global _storage
    if _storage is None:
        if settings.storage_backend == "gcs":
            from app.storage.gcs import GcsStorage

            _storage = GcsStorage()
        elif settings.storage_backend == "s3":
            from app.storage.s3 import S3Storage

            _storage = S3Storage()
        else:
            from app.storage.local import LocalStorage

            _storage = LocalStorage()
    return _storage

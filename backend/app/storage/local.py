"""Filesystem storage for local development.

Keys are namespaced per candidate so a listing never mixes candidates, and the
directory is gitignored — candidate files must not reach version control.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.core.config import settings
from app.storage.base import ObjectStorage, StoredObject


class LocalStorage(ObjectStorage):
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or settings.storage_local_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Resolve and confirm containment: a key is attacker-influenced input.
        target = (self.root / key).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError(f"Refusing to access a key outside the storage root: {key}")
        return target

    def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return StoredObject(
            key=key, size_bytes=len(data), checksum=hashlib.sha256(data).hexdigest()
        )

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        target = self._path(key)
        if target.exists():
            target.unlink()

    def signed_url(self, key: str, ttl_seconds: int = 300) -> str:
        # No signing locally: the API streams the bytes itself after authorizing.
        return f"{settings.api_v1_prefix}/resumes/file/{key}"

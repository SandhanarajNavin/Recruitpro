"""Object storage for original resume files.

Interface first, two implementations. The file never leaves the private bucket: the
API mints a short-lived signed URL after an authorization check, so a browser never
holds a storage credential (architecture doc §19).
"""

from app.storage.base import ObjectStorage, StoredObject
from app.storage.factory import get_storage

__all__ = ["ObjectStorage", "StoredObject", "get_storage"]

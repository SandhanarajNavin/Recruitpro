"""Google Cloud Storage. Imported lazily so the client is only needed when configured.

The backend for Cloud Run, where the container filesystem is ephemeral and per
instance: a resume written by the API would not survive the request that stored it,
and the worker — a different service — would never see it at all. ``local`` is a
development convenience, not a deployment option there.

Chosen over pointing the S3 backend at GCS's interop endpoint, which would work but
needs long-lived HMAC keys. This authenticates through Application Default
Credentials, the same path ``GeminiEmbedder`` already uses, so a deployment with
Workload Identity carries no key material at all.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

from app.core.config import settings
from app.core.logging import get_logger
from app.storage.base import ObjectStorage, StoredObject

logger = get_logger(__name__)


class GcsStorage(ObjectStorage):
    def __init__(self) -> None:
        if not settings.gcs_bucket:
            raise ValueError("storage_backend='gcs' requires GCS_BUCKET.")
        from google.cloud import storage  # imported here: only needed when configured

        self.bucket_name = settings.gcs_bucket
        # project=None lets the client take it from ADC, so the bucket's project and
        # the credentials cannot disagree.
        self._client = storage.Client(project=settings.google_cloud_project or None)
        self._bucket = self._client.bucket(self.bucket_name)

    def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        blob = self._bucket.blob(key)
        blob.upload_from_string(data, content_type=content_type or "application/octet-stream")
        return StoredObject(
            key=key, size_bytes=len(data), checksum=hashlib.sha256(data).hexdigest()
        )

    def get(self, key: str) -> bytes:
        from google.cloud.exceptions import NotFound

        try:
            return self._bucket.blob(key).download_as_bytes()
        except NotFound as exc:
            # The download endpoint turns this into a 410, so it has to be the same
            # exception the local backend raises for a missing file.
            raise FileNotFoundError(key) from exc

    def delete(self, key: str) -> None:
        from google.cloud.exceptions import NotFound

        try:
            self._bucket.blob(key).delete()
        except NotFound:
            # Already gone is the desired end state, not an error.
            logger.info("Blob %s was already absent from %s", key, self.bucket_name)

    def signed_url(self, key: str, ttl_seconds: int = 300) -> str:
        """A time-limited URL, or the streaming endpoint when one cannot be signed.

        V4 signing needs a private key, which Workload Identity deliberately does not
        provide — the metadata server holds no key material. Rather than fail, fall
        back to the API's own download route, which authorises the recruiter and
        streams the bytes. That is what the local backend does too, so the bucket
        never needs a public read path either way.
        """
        try:
            return self._bucket.blob(key).generate_signed_url(
                version="v4", expiration=timedelta(seconds=ttl_seconds), method="GET"
            )
        except Exception as exc:  # noqa: BLE001 - any signing failure degrades the same way
            logger.info(
                "Cannot sign a GCS URL (%s); falling back to the streaming endpoint", exc
            )
            return f"{settings.api_v1_prefix}/resumes/file/{key}"

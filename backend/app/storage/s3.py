"""S3 storage. Imported lazily so boto3 is only needed when actually configured."""

from __future__ import annotations

import hashlib

from app.core.config import settings
from app.storage.base import ObjectStorage, StoredObject


class S3Storage(ObjectStorage):
    def __init__(self) -> None:
        if not settings.s3_bucket:
            raise ValueError("storage_backend='s3' requires S3_BUCKET.")
        import boto3

        self.bucket = settings.s3_bucket
        self.client = boto3.client("s3", region_name=settings.s3_region)

    def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            ServerSideEncryption="AES256",
        )
        return StoredObject(
            key=key, size_bytes=len(data), checksum=hashlib.sha256(data).hexdigest()
        )

    def get(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def signed_url(self, key: str, ttl_seconds: int = 300) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=ttl_seconds,
        )

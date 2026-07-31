"""S3-compatible object storage (ADR-010 P11).

Uses boto3 when installed; credentials via env/credential_ref only — never inline.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from taksitlio.media.storage import LocalObjectStorage, ObjectStorage


class S3CompatibleObjectStorage:
    """Thin S3 put + CDN URL mapper.

    Requires ``boto3``. Bucket/endpoint from env — no secrets in code.
    """

    def __init__(
        self,
        *,
        bucket: str,
        cdn_base_url: str,
        prefix: str = "",
        endpoint_url: Optional[str] = None,
        region_name: Optional[str] = None,
        client: Any = None,
    ) -> None:
        if not bucket:
            raise ValueError("bucket required")
        self.bucket = bucket
        self.cdn_base_url = cdn_base_url.rstrip("/")
        self.prefix = prefix.strip("/")
        if client is not None:
            self._client = client
        else:
            try:
                import boto3  # type: ignore
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "boto3 required for S3CompatibleObjectStorage; pip install boto3"
                ) from exc
            kwargs: dict[str, Any] = {}
            if endpoint_url:
                kwargs["endpoint_url"] = endpoint_url
            if region_name:
                kwargs["region_name"] = region_name
            self._client = boto3.client("s3", **kwargs)

    def _key(self, key: str) -> str:
        k = key.lstrip("/")
        if self.prefix:
            return f"{self.prefix}/{k}"
        return k

    def put(self, key: str, data: bytes, *, content_type: str) -> str:
        storage_key = self._key(key)
        self._client.put_object(
            Bucket=self.bucket,
            Key=storage_key,
            Body=data,
            ContentType=content_type or "application/octet-stream",
        )
        return storage_key

    def cdn_url_for(self, key: str) -> str:
        return f"{self.cdn_base_url}/{key.lstrip('/')}"


def build_object_storage_from_env(
    *,
    default_local_root: str = "/tmp/taksitlio-media",
) -> ObjectStorage:
    """Select Local vs S3 from ``OBJECT_STORAGE_BACKEND``."""

    backend = (os.environ.get("OBJECT_STORAGE_BACKEND") or "local").strip().lower()
    cdn = os.environ.get("CDN_BASE_URL", "https://cdn.example.test")
    if backend in {"s3", "s3_compatible", "minio"}:
        return S3CompatibleObjectStorage(
            bucket=os.environ.get("S3_BUCKET", "").strip(),
            cdn_base_url=cdn,
            prefix=os.environ.get("S3_PREFIX", "taksitlio"),
            endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
            region_name=os.environ.get("S3_REGION") or None,
        )
    root = os.environ.get("MEDIA_STORAGE_ROOT") or default_local_root
    return LocalObjectStorage(root, cdn_base_url=cdn)


__all__ = [
    "S3CompatibleObjectStorage",
    "build_object_storage_from_env",
]

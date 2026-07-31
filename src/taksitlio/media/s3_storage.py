"""S3-compatible object storage (ADR-010 P11/P16).

Uses boto3 when installed; credentials via env/IAM only — never inline.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from taksitlio.media.config import load_object_storage_config
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
                    "boto3 required for S3CompatibleObjectStorage; pip install '.[storage]'"
                ) from exc
            kwargs: dict[str, Any] = {}
            if endpoint_url:
                kwargs["endpoint_url"] = endpoint_url
            if region_name:
                kwargs["region_name"] = region_name
            self._client = boto3.client("s3", **kwargs)

    def _key(self, key: str) -> str:
        k = key.lstrip("/")
        if self.prefix and not k.startswith(f"{self.prefix}/") and k != self.prefix:
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
        storage_key = self._key(key)
        return f"{self.cdn_base_url}/{storage_key.lstrip('/')}"


def build_object_storage_from_env(
    *,
    default_local_root: str = "/tmp/taksitlio-media",
    validate: bool = True,
    strict_cdn: bool = False,
) -> ObjectStorage:
    """Select Local vs S3 from ``OBJECT_STORAGE_BACKEND``."""

    cfg = load_object_storage_config(default_local_root=default_local_root)
    if validate:
        cfg.validate(strict=strict_cdn)
    if cfg.backend == "s3":
        return S3CompatibleObjectStorage(
            bucket=cfg.bucket or "",
            cdn_base_url=cfg.cdn_base_url,
            prefix=cfg.prefix,
            endpoint_url=cfg.endpoint_url,
            region_name=cfg.region_name,
        )
    root = cfg.media_root or default_local_root
    if not os.environ.get("MEDIA_STORAGE_ROOT"):
        os.environ["MEDIA_STORAGE_ROOT"] = root
    return LocalObjectStorage(root, cdn_base_url=cfg.cdn_base_url)


__all__ = [
    "S3CompatibleObjectStorage",
    "build_object_storage_from_env",
]

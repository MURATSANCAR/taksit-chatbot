"""Object storage configuration + health (ADR-010 P16).

Credentials stay in env / IAM — never inline in app code or DB.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

PLACEHOLDER_CDN_MARKERS = (
    "cdn.example.test",
    "example.com",
    "changeme",
)


@dataclass(frozen=True)
class ObjectStorageConfig:
    backend: str  # local | s3
    cdn_base_url: str
    media_root: Optional[str] = None
    bucket: Optional[str] = None
    prefix: str = "taksitlio"
    endpoint_url: Optional[str] = None
    region_name: Optional[str] = None

    @property
    def is_placeholder_cdn(self) -> bool:
        low = self.cdn_base_url.casefold()
        return any(m in low for m in PLACEHOLDER_CDN_MARKERS)

    def validate(self, *, strict: bool = False) -> None:
        if not self.cdn_base_url.strip():
            raise ValueError("CDN_BASE_URL required")
        if self.backend == "local":
            if not self.media_root:
                raise ValueError("MEDIA_STORAGE_ROOT required for local backend")
            return
        if self.backend in {"s3", "s3_compatible", "minio"}:
            if not (self.bucket or "").strip():
                raise ValueError("S3_BUCKET required when OBJECT_STORAGE_BACKEND=s3")
            if strict and self.is_placeholder_cdn:
                raise ValueError(
                    "CDN_BASE_URL looks like a placeholder; set the real CDN origin"
                )
            return
        raise ValueError(f"unsupported OBJECT_STORAGE_BACKEND: {self.backend}")


@dataclass(frozen=True)
class ObjectStorageStatus:
    backend: str
    cdn_base_url: str
    ready: bool
    detail: str
    placeholder_cdn: bool
    bucket: Optional[str] = None
    prefix: Optional[str] = None
    media_root: Optional[str] = None
    local_cdn_mount: bool = False


def load_object_storage_config(
    *,
    default_local_root: Optional[str] = None,
) -> ObjectStorageConfig:
    backend = (os.environ.get("OBJECT_STORAGE_BACKEND") or "local").strip().lower()
    cdn = (os.environ.get("CDN_BASE_URL") or "https://cdn.example.test").strip()
    root = os.environ.get("MEDIA_STORAGE_ROOT") or default_local_root
    if backend in {"s3", "s3_compatible", "minio"}:
        return ObjectStorageConfig(
            backend="s3",
            cdn_base_url=cdn.rstrip("/"),
            bucket=(os.environ.get("S3_BUCKET") or "").strip() or None,
            prefix=(os.environ.get("S3_PREFIX") or "taksitlio").strip().strip("/")
            or "taksitlio",
            endpoint_url=(os.environ.get("S3_ENDPOINT_URL") or "").strip() or None,
            region_name=(os.environ.get("S3_REGION") or "").strip() or None,
        )
    if not root:
        root = tempfile.mkdtemp(prefix="taksitlio-media-")
    return ObjectStorageConfig(
        backend="local",
        cdn_base_url=cdn.rstrip("/"),
        media_root=root,
    )


def describe_object_storage(
    storage: Any,
    *,
    config: Optional[ObjectStorageConfig] = None,
) -> ObjectStorageStatus:
    cfg = config or load_object_storage_config()
    local_mount = cfg.backend == "local" and bool(cfg.media_root)
    if cfg.backend == "local":
        root = Path(cfg.media_root or ".")
        ready = root.exists() and os.access(root, os.W_OK)
        detail = "writable" if ready else f"not writable: {root}"
        return ObjectStorageStatus(
            backend="local",
            cdn_base_url=cfg.cdn_base_url,
            ready=ready,
            detail=detail,
            placeholder_cdn=cfg.is_placeholder_cdn,
            media_root=str(root),
            local_cdn_mount=local_mount,
        )

    bucket = getattr(storage, "bucket", cfg.bucket)
    prefix = getattr(storage, "prefix", cfg.prefix)
    return ObjectStorageStatus(
        backend="s3",
        cdn_base_url=cfg.cdn_base_url,
        ready=bool(bucket),
        detail="configured" if bucket else "missing S3_BUCKET",
        placeholder_cdn=cfg.is_placeholder_cdn,
        bucket=bucket,
        prefix=prefix,
        local_cdn_mount=False,
    )


def probe_object_storage(storage: Any, *, config: Optional[ObjectStorageConfig] = None) -> ObjectStorageStatus:
    """Light connectivity check — head_bucket for S3, writable dir for local."""

    base = describe_object_storage(storage, config=config)
    cfg = config or load_object_storage_config()
    if cfg.backend == "local":
        return base
    client = getattr(storage, "_client", None)
    bucket = getattr(storage, "bucket", None)
    if client is None or not bucket:
        return ObjectStorageStatus(
            backend=base.backend,
            cdn_base_url=base.cdn_base_url,
            ready=False,
            detail="s3 client/bucket unavailable",
            placeholder_cdn=base.placeholder_cdn,
            bucket=base.bucket,
            prefix=base.prefix,
        )
    try:
        client.head_bucket(Bucket=bucket)
        return ObjectStorageStatus(
            backend=base.backend,
            cdn_base_url=base.cdn_base_url,
            ready=True,
            detail="head_bucket ok",
            placeholder_cdn=base.placeholder_cdn,
            bucket=base.bucket,
            prefix=base.prefix,
        )
    except Exception as exc:  # noqa: BLE001
        return ObjectStorageStatus(
            backend=base.backend,
            cdn_base_url=base.cdn_base_url,
            ready=False,
            detail=f"head_bucket failed: {type(exc).__name__}",
            placeholder_cdn=base.placeholder_cdn,
            bucket=base.bucket,
            prefix=base.prefix,
        )


__all__ = [
    "ObjectStorageConfig",
    "ObjectStorageStatus",
    "describe_object_storage",
    "load_object_storage_config",
    "probe_object_storage",
]

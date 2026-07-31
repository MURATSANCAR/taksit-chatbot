"""Admin media / object-storage health (ADR-010 P16)."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from taksitlio.api.deps import container_from

router = APIRouter(tags=["admin-media"])


@router.get("/media/storage")
async def media_storage_status(
    request: Request, probe: bool = False
) -> Dict[str, Any]:
    """Describe object storage + CDN config. Optional live S3 head_bucket probe."""

    from taksitlio.media.config import (
        describe_object_storage,
        load_object_storage_config,
        probe_object_storage,
    )

    container = container_from(request)
    storage = container.extras.get("media_storage")
    if storage is None:
        raise HTTPException(status_code=501, detail="media_storage not configured")
    cfg = load_object_storage_config()
    status = (
        probe_object_storage(storage, config=cfg)
        if probe
        else describe_object_storage(storage, config=cfg)
    )
    return {
        "backend": status.backend,
        "cdn_base_url": status.cdn_base_url,
        "ready": status.ready,
        "detail": status.detail,
        "placeholder_cdn": status.placeholder_cdn,
        "bucket": status.bucket,
        "prefix": status.prefix,
        "media_root": status.media_root,
        "local_cdn_mount": status.local_cdn_mount,
        "probed": probe,
        "note": "Credentials via env/IAM only; chatbot images must use CDN URLs",
    }

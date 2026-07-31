"""Ingest image bytes into object storage (ADR-010 §38) — never hotlink."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import httpx

from taksitlio.ingestion.errors import MediaFetchFailed
from taksitlio.media.hashing import (
    decode_dimensions,
    perceptual_hash_hex,
    sha256_hex,
    sniff_mime,
)
from taksitlio.media.quality import MediaQualityPolicy, evaluate_image_quality
from taksitlio.media.storage import ObjectStorage
from taksitlio.media.types import MediaAssetDraft, MediaStatus
from taksitlio.media.variants import encode_webp_variants


@dataclass(frozen=True)
class MediaIngestOutcome:
    draft: MediaAssetDraft
    skipped_duplicate_sha: bool = False


async def download_image(
    source_url: str,
    *,
    timeout_seconds: float = 30.0,
    max_bytes: int = 15 * 1024 * 1024,
) -> bytes:
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
            async with client.stream("GET", source_url) as resp:
                resp.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise MediaFetchFailed("image exceeds max_bytes", detail=source_url)
                    chunks.append(chunk)
                return b"".join(chunks)
    except MediaFetchFailed:
        raise
    except httpx.HTTPError as exc:
        raise MediaFetchFailed(str(exc), detail=source_url) from exc


def ingest_image_bytes(
    data: bytes,
    *,
    source_url: str,
    storage: ObjectStorage,
    known_sha256: Optional[set[str]] = None,
    policy: Optional[MediaQualityPolicy] = None,
    source_reference: Optional[str] = None,
) -> MediaIngestOutcome:
    """Validate → hash → quality → store original + WebP variants.

    Chatbot must use ``draft.cdn_url`` / variant CDN URLs, never ``source_url``.
    """

    pol = policy or MediaQualityPolicy()
    digest = sha256_hex(data)
    if known_sha256 and digest in known_sha256:
        return MediaIngestOutcome(
            draft=MediaAssetDraft(
                source_url=source_url,
                sha256=digest,
                mime_type=sniff_mime(data),
                width=None,
                height=None,
                file_size=len(data),
                perceptual_hash=None,
                quality_score=None,
                status=MediaStatus.READY,
                source_reference=source_reference,
            ),
            skipped_duplicate_sha=True,
        )

    mime = sniff_mime(data)
    if mime is None:
        return MediaIngestOutcome(
            draft=MediaAssetDraft(
                source_url=source_url,
                sha256=digest,
                mime_type=None,
                width=None,
                height=None,
                file_size=len(data),
                perceptual_hash=None,
                quality_score=0.0,
                status=MediaStatus.QUARANTINED,
                source_reference=source_reference,
                quality_detail={"reason": "unsupported_or_invalid_mime"},
            )
        )

    width, height, decode_ok = decode_dimensions(data)
    quality = evaluate_image_quality(
        width=width,
        height=height,
        file_size=len(data),
        decode_ok=decode_ok,
        policy=pol,
    )
    phash = perceptual_hash_hex(data)

    if not quality.decode_ok or not quality.size_ok:
        status = MediaStatus.QUARANTINED
    elif quality.acceptable_for_primary:
        status = MediaStatus.READY
    else:
        status = MediaStatus.QUARANTINED

    filename = _filename_from_url(source_url) or f"{digest[:16]}.bin"
    storage_key = f"media/original/{digest[:2]}/{digest}/{filename}"
    storage.put(storage_key, data, content_type=mime)
    cdn_url = storage.cdn_url_for(storage_key)

    variant_rows: list[dict] = []
    if status is MediaStatus.READY and width and height:
        for plan in encode_webp_variants(data, source_width=width, source_height=height):
            vkey = f"media/variants/{digest[:2]}/{digest}/{plan.variant_code}.webp"
            storage.put(vkey, plan.data, content_type=plan.mime_type)
            variant_rows.append(
                {
                    "variant_code": plan.variant_code,
                    "width": plan.width,
                    "height": plan.height,
                    "mime_type": plan.mime_type,
                    "storage_key": vkey,
                    "cdn_url": storage.cdn_url_for(vkey),
                    "file_size": len(plan.data),
                }
            )

    draft = MediaAssetDraft(
        source_url=source_url,
        sha256=digest,
        mime_type=mime,
        width=width,
        height=height,
        file_size=len(data),
        perceptual_hash=phash,
        quality_score=quality.quality_score,
        status=status,
        storage_key=storage_key,
        cdn_url=cdn_url,
        source_reference=source_reference,
        variants=tuple(variant_rows),
        quality_detail=dict(quality.detail),
    )
    return MediaIngestOutcome(draft=draft)


def _filename_from_url(url: str) -> Optional[str]:
    path = urlparse(url).path
    name = path.rsplit("/", 1)[-1] if path else ""
    return name or None


__all__ = ["MediaIngestOutcome", "download_image", "ingest_image_bytes"]

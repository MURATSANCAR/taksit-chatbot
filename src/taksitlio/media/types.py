"""Media domain enums / drafts (ADR-010 §38)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional


class MediaStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"
    IMAGE_UNAVAILABLE = "IMAGE_UNAVAILABLE"
    FAILED = "FAILED"


class MediaRole(str, Enum):
    PRIMARY = "PRIMARY"
    GALLERY = "GALLERY"
    THUMBNAIL = "THUMBNAIL"
    PACKAGING = "PACKAGING"
    DETAIL = "DETAIL"
    COLOR_VARIANT = "COLOR_VARIANT"


@dataclass(frozen=True)
class MediaAssetDraft:
    source_url: str
    sha256: str
    mime_type: Optional[str]
    width: Optional[int]
    height: Optional[int]
    file_size: int
    perceptual_hash: Optional[str]
    quality_score: Optional[float]
    status: MediaStatus
    storage_key: Optional[str] = None
    cdn_url: Optional[str] = None
    source_reference: Optional[str] = None
    variants: tuple[dict[str, Any], ...] = ()
    quality_detail: Mapping[str, Any] = field(default_factory=dict)


__all__ = ["MediaAssetDraft", "MediaRole", "MediaStatus"]

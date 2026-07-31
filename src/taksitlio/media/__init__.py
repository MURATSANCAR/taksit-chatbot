"""ADR-010 media pipeline (P2) — no hotlinked merchant images in chatbot."""

from taksitlio.media.pipeline import MediaIngestOutcome, ingest_image_bytes
from taksitlio.media.primary import IMAGE_UNAVAILABLE, select_primary_candidate
from taksitlio.media.quality import MediaQualityPolicy, evaluate_image_quality
from taksitlio.media.storage import LocalObjectStorage, ObjectStorage
from taksitlio.media.types import MediaAssetDraft, MediaRole, MediaStatus

__all__ = [
    "IMAGE_UNAVAILABLE",
    "LocalObjectStorage",
    "MediaAssetDraft",
    "MediaIngestOutcome",
    "MediaQualityPolicy",
    "MediaRole",
    "MediaStatus",
    "ObjectStorage",
    "evaluate_image_quality",
    "ingest_image_bytes",
    "select_primary_candidate",
]

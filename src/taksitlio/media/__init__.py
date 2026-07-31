"""ADR-010 media pipeline (P2) — no hotlinked merchant images in chatbot."""

from taksitlio.media.pipeline import MediaIngestOutcome, ingest_image_bytes
from taksitlio.media.primary import IMAGE_UNAVAILABLE, select_primary_candidate
from taksitlio.media.quality import MediaQualityPolicy, evaluate_image_quality
from taksitlio.media.s3_storage import S3CompatibleObjectStorage, build_object_storage_from_env
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
    "S3CompatibleObjectStorage",
    "build_object_storage_from_env",
    "evaluate_image_quality",
    "ingest_image_bytes",
    "select_primary_candidate",
]

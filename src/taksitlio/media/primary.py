"""Primary image selection among candidates (ADR-010 §39–40)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from taksitlio.media.types import MediaAssetDraft, MediaStatus

IMAGE_UNAVAILABLE = "IMAGE_UNAVAILABLE"


@dataclass(frozen=True)
class PrimarySelection:
    status: str  # READY | IMAGE_UNAVAILABLE
    asset: Optional[MediaAssetDraft]
    reason: str


def select_primary_candidate(
    candidates: Sequence[MediaAssetDraft],
    *,
    near_duplicate_hamming_max: int = 5,
) -> PrimarySelection:
    """Pick best READY asset; never invent a placeholder image."""

    _ = near_duplicate_hamming_max
    ready = [c for c in candidates if c.status is MediaStatus.READY]
    if not ready:
        return PrimarySelection(
            status=IMAGE_UNAVAILABLE,
            asset=None,
            reason="no_ready_primary",
        )

    def _score(c: MediaAssetDraft) -> tuple:
        return (
            c.quality_score or 0.0,
            c.width or 0,
            c.height or 0,
            -(c.file_size or 0),
        )

    best = max(ready, key=_score)
    return PrimarySelection(status="READY", asset=best, reason="quality_score")


__all__ = ["IMAGE_UNAVAILABLE", "PrimarySelection", "select_primary_candidate"]

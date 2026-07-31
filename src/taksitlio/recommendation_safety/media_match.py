"""Media–product match gate (ADR-012 §12)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


MEDIA_PRODUCT_MATCH_UNCERTAIN = "MEDIA_PRODUCT_MATCH_UNCERTAIN"


class MediaMatchStatus(str, Enum):
    MATCHED = "MATCHED"
    UNCERTAIN = "UNCERTAIN"
    MISMATCH = "MISMATCH"


@dataclass(frozen=True)
class MediaMatchSignals:
    variant_match: bool = True
    color_match: bool = True
    model_family_match: bool = True
    is_pack_shot: bool = False
    is_category_image: bool = False
    is_merchant_logo: bool = False
    confidence: float = 1.0


@dataclass(frozen=True)
class MediaMatchDecision:
    status: MediaMatchStatus
    allow_primary: bool
    reason: str


def evaluate_media_match(signals: MediaMatchSignals) -> MediaMatchDecision:
    if signals.is_merchant_logo:
        return MediaMatchDecision(
            status=MediaMatchStatus.MISMATCH,
            allow_primary=False,
            reason="merchant_logo_as_product",
        )
    if signals.is_category_image:
        return MediaMatchDecision(
            status=MediaMatchStatus.MISMATCH,
            allow_primary=False,
            reason="category_image",
        )
    if not signals.variant_match or not signals.color_match or not signals.model_family_match:
        return MediaMatchDecision(
            status=MediaMatchStatus.MISMATCH,
            allow_primary=False,
            reason="variant_or_color_or_family_mismatch",
        )
    if signals.is_pack_shot or signals.confidence < 0.75:
        return MediaMatchDecision(
            status=MediaMatchStatus.UNCERTAIN,
            allow_primary=False,
            reason=MEDIA_PRODUCT_MATCH_UNCERTAIN,
        )
    return MediaMatchDecision(
        status=MediaMatchStatus.MATCHED,
        allow_primary=True,
        reason="matched",
    )


def primary_image_url(
    *,
    candidate_url: Optional[str],
    decision: MediaMatchDecision,
) -> Optional[str]:
    if not decision.allow_primary:
        return None
    return candidate_url


__all__ = [
    "MEDIA_PRODUCT_MATCH_UNCERTAIN",
    "MediaMatchDecision",
    "MediaMatchSignals",
    "MediaMatchStatus",
    "evaluate_media_match",
    "primary_image_url",
]

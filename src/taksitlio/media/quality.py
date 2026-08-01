"""Primary image quality policy (ADR-010 §39).

Prefer versioned MediaEdgeRules from media_quality_policies (P2-LIVE).
Legacy MediaQualityPolicy remains for backward-compatible callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class MediaQualityPolicy:
    # Legacy square-ish defaults — prefer policy store short/long edge rules.
    min_width: int = 600
    min_height: int = 600
    preferred_width: int = 1000
    aspect_min: float = 0.75
    aspect_max: float = 1.33
    max_bytes: int = 15 * 1024 * 1024
    # P2-LIVE optional edge overrides (when set, square is not required)
    min_short_edge: Optional[int] = None
    min_long_edge: Optional[int] = None


@dataclass(frozen=True)
class MediaQualityResult:
    min_width_ok: bool
    min_height_ok: bool
    preferred_width_ok: bool
    aspect_ratio_ok: bool
    decode_ok: bool
    size_ok: bool
    quality_score: float
    detail: Mapping[str, Any]

    @property
    def acceptable_for_primary(self) -> bool:
        return (
            self.decode_ok
            and self.size_ok
            and self.min_width_ok
            and self.min_height_ok
            and self.aspect_ratio_ok
        )


def evaluate_image_quality(
    *,
    width: Optional[int],
    height: Optional[int],
    file_size: int,
    decode_ok: bool,
    policy: Optional[MediaQualityPolicy] = None,
) -> MediaQualityResult:
    pol = policy or MediaQualityPolicy()
    size_ok = 0 < file_size <= pol.max_bytes
    w = width or 0
    h = height or 0
    short_e = min(w, h) if w and h else 0
    long_e = max(w, h) if w and h else 0
    if pol.min_short_edge is not None or pol.min_long_edge is not None:
        # Event-driven adaptive media policy: short/long edge, not forced square.
        min_short = pol.min_short_edge if pol.min_short_edge is not None else pol.min_width
        min_long = pol.min_long_edge if pol.min_long_edge is not None else pol.min_height
        min_w = decode_ok and short_e >= min_short
        min_h = decode_ok and long_e >= min_long
    else:
        min_w = decode_ok and w >= pol.min_width
        min_h = decode_ok and h >= pol.min_height
    pref_w = decode_ok and w >= pol.preferred_width
    aspect_ok = False
    aspect = None
    if decode_ok and w > 0 and h > 0:
        aspect = w / h
        aspect_ok = pol.aspect_min <= aspect <= pol.aspect_max

    score = 0.0
    if decode_ok:
        score += 0.35
    if min_w and min_h:
        score += 0.25
    if pref_w:
        score += 0.15
    if aspect_ok:
        score += 0.15
    if size_ok:
        score += 0.10

    return MediaQualityResult(
        min_width_ok=min_w,
        min_height_ok=min_h,
        preferred_width_ok=pref_w,
        aspect_ratio_ok=aspect_ok,
        decode_ok=decode_ok,
        size_ok=size_ok,
        quality_score=round(score, 4),
        detail={
            "width": width,
            "height": height,
            "short_edge": short_e,
            "long_edge": long_e,
            "aspect": aspect,
            "file_size": file_size,
        },
    )


__all__ = ["MediaQualityPolicy", "MediaQualityResult", "evaluate_image_quality"]

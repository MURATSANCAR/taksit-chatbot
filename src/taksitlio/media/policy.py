"""Versioned media quality policy — short/long edge (no forced square)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class MediaEdgeRules:
    min_short_edge: int = 400
    min_long_edge: int = 600
    min_aspect_ratio: float = 0.33
    max_aspect_ratio: float = 3.0
    require_decode: bool = True
    require_product_relation: bool = True
    reject_blank: bool = True
    max_bytes: int = 15 * 1024 * 1024

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "MediaEdgeRules":
        return cls(
            min_short_edge=int(data.get("min_short_edge", 400) or 400),
            min_long_edge=int(data.get("min_long_edge", 600) or 600),
            min_aspect_ratio=float(data.get("min_aspect_ratio", 0.33) or 0.33),
            max_aspect_ratio=float(data.get("max_aspect_ratio", 3.0) or 3.0),
            require_decode=bool(data.get("require_decode", True)),
            require_product_relation=bool(data.get("require_product_relation", True)),
            reject_blank=bool(data.get("reject_blank", True)),
            max_bytes=int(data.get("max_bytes", 15 * 1024 * 1024) or 15 * 1024 * 1024),
        )


@dataclass(frozen=True)
class VersionedMediaQualityPolicy:
    policy_code: str
    version: int
    card_ready: MediaEdgeRules
    detail_ready: MediaEdgeRules
    status: str = "ACTIVE"

    @property
    def media_quality_policy_version(self) -> str:
        return f"{self.policy_code}:v{self.version}"


@dataclass(frozen=True)
class MediaReadinessResult:
    card_ready: bool
    detail_ready: bool
    reasons: tuple[str, ...]
    policy_version: str
    detail: Mapping[str, Any]


def evaluate_media_readiness(
    *,
    width: Optional[int],
    height: Optional[int],
    file_size: int,
    decode_ok: bool,
    has_product_relation: bool,
    blank_score: float = 0.0,
    policy: VersionedMediaQualityPolicy,
) -> MediaReadinessResult:
    """CARD_READY / DETAIL_READY from policy store — not square-forced."""

    reasons: list[str] = []
    w = width or 0
    h = height or 0
    short_e = min(w, h) if w and h else 0
    long_e = max(w, h) if w and h else 0
    aspect = (w / h) if w and h else None

    def _check(rules: MediaEdgeRules) -> tuple[bool, list[str]]:
        local: list[str] = []
        if rules.require_decode and not decode_ok:
            local.append("decode_failed")
        if rules.require_product_relation and not has_product_relation:
            local.append("missing_product_relation")
        if rules.reject_blank and blank_score >= 0.9:
            local.append("blank_image")
        if not (0 < file_size <= rules.max_bytes):
            local.append("size_out_of_range")
        if short_e < rules.min_short_edge:
            local.append("short_edge_below_minimum")
        if long_e < rules.min_long_edge:
            local.append("long_edge_below_minimum")
        if aspect is not None and not (
            rules.min_aspect_ratio <= aspect <= rules.max_aspect_ratio
        ):
            local.append("aspect_ratio_out_of_range")
        return (not local), local

    card_ok, card_reasons = _check(policy.card_ready)
    detail_ok, detail_reasons = _check(policy.detail_ready)
    reasons.extend(f"card:{r}" for r in card_reasons)
    reasons.extend(f"detail:{r}" for r in detail_reasons)
    return MediaReadinessResult(
        card_ready=card_ok,
        detail_ready=detail_ok,
        reasons=tuple(reasons),
        policy_version=policy.media_quality_policy_version,
        detail={
            "width": width,
            "height": height,
            "short_edge": short_e,
            "long_edge": long_e,
            "aspect": aspect,
            "file_size": file_size,
        },
    )


def default_seed_policy() -> VersionedMediaQualityPolicy:
    """Seed only — production must load ACTIVE row from media_quality_policies."""

    return VersionedMediaQualityPolicy(
        policy_code="default",
        version=1,
        card_ready=MediaEdgeRules(),
        detail_ready=MediaEdgeRules(min_short_edge=800, min_long_edge=1000),
    )


__all__ = [
    "MediaEdgeRules",
    "MediaReadinessResult",
    "VersionedMediaQualityPolicy",
    "default_seed_policy",
    "evaluate_media_readiness",
]

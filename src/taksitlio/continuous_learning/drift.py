"""Catalog drift detection — freeze new mappings on dramatic taxonomy shifts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional


class DriftType(str, Enum):
    SOURCE_TAXONOMY_DRIFT = "SOURCE_TAXONOMY_DRIFT"
    BRAND_DISTRIBUTION_DRIFT = "BRAND_DISTRIBUTION_DRIFT"
    ATTRIBUTE_DISTRIBUTION_DRIFT = "ATTRIBUTE_DISTRIBUTION_DRIFT"
    PRICE_DISTRIBUTION_DRIFT = "PRICE_DISTRIBUTION_DRIFT"
    STOCK_DISTRIBUTION_DRIFT = "STOCK_DISTRIBUTION_DRIFT"
    QUERY_VOCABULARY_DRIFT = "QUERY_VOCABULARY_DRIFT"
    ALIAS_DRIFT = "ALIAS_DRIFT"
    RANKING_FEEDBACK_DRIFT = "RANKING_FEEDBACK_DRIFT"
    MEDIA_QUALITY_DRIFT = "MEDIA_QUALITY_DRIFT"


@dataclass(frozen=True)
class DriftAlarm:
    drift_type: DriftType
    severity: str
    merchant_id: Optional[int]
    baseline: Mapping[str, float]
    observed: Mapping[str, float]
    freeze_new_mappings: bool
    preserve_validated_mappings: bool
    message: str


def jensen_shannon_proxy(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    """Lightweight distribution distance in [0, 1]."""

    keys = set(a) | set(b)
    if not keys:
        return 0.0
    total_a = sum(max(0.0, a.get(k, 0.0)) for k in keys) or 1.0
    total_b = sum(max(0.0, b.get(k, 0.0)) for k in keys) or 1.0
    dist = 0.0
    for k in keys:
        pa = max(0.0, a.get(k, 0.0)) / total_a
        pb = max(0.0, b.get(k, 0.0)) / total_b
        dist += abs(pa - pb)
    return min(1.0, dist / 2.0)


def detect_taxonomy_drift(
    *,
    baseline_path_share: Mapping[str, float],
    observed_path_share: Mapping[str, float],
    merchant_id: Optional[int] = None,
    critical_threshold: float = 0.35,
    warning_threshold: float = 0.20,
) -> Optional[DriftAlarm]:
    score = jensen_shannon_proxy(baseline_path_share, observed_path_share)
    if score < warning_threshold:
        return None
    severity = "CRITICAL" if score >= critical_threshold else "WARNING"
    return DriftAlarm(
        drift_type=DriftType.SOURCE_TAXONOMY_DRIFT,
        severity=severity,
        merchant_id=merchant_id,
        baseline=dict(baseline_path_share),
        observed=dict(observed_path_share),
        freeze_new_mappings=severity == "CRITICAL",
        preserve_validated_mappings=True,
        message=f"source_taxonomy_drift score={score:.3f}",
    )


def action_for_drift(alarm: DriftAlarm) -> Mapping[str, object]:
    return {
        "freeze_new_mappings": alarm.freeze_new_mappings,
        "preserve_validated_mappings": alarm.preserve_validated_mappings,
        "quarantine_new_candidates": alarm.freeze_new_mappings,
        "drift_type": alarm.drift_type.value,
        "severity": alarm.severity,
    }


__all__ = [
    "DriftAlarm",
    "DriftType",
    "action_for_drift",
    "detect_taxonomy_drift",
    "jensen_shannon_proxy",
]

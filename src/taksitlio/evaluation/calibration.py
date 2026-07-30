"""Calibration metrics: Brier score + Expected Calibration Error.

Confidence buckets are taken from the top-1 candidate score. When the
matcher returns AMBIGUOUS / NO_MATCH we treat "auto-select confidence"
as the top score anyway — otherwise we would silently exclude the
cases whose confidence matters most for calibration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class CalibrationSummary:
    brier: float
    ece: float
    bucket_count: int
    sample_size: int

    def to_dict(self) -> dict:
        return {
            "brier": self.brier,
            "ece": self.ece,
            "bucket_count": self.bucket_count,
            "sample_size": self.sample_size,
        }


def brier_score(pairs: Sequence[tuple[float, bool]]) -> float:
    if not pairs:
        return 0.0
    total = 0.0
    for confidence, correct in pairs:
        total += (confidence - (1.0 if correct else 0.0)) ** 2
    return total / len(pairs)


def expected_calibration_error(
    pairs: Sequence[tuple[float, bool]],
    *,
    bucket_count: int = 10,
) -> float:
    if not pairs:
        return 0.0
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bucket_count)]
    for confidence, correct in pairs:
        idx = min(bucket_count - 1, max(0, int(confidence * bucket_count)))
        buckets[idx].append((confidence, correct))
    total = len(pairs)
    ece = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        avg_conf = sum(c for c, _ in bucket) / len(bucket)
        acc = sum(1 for _, correct in bucket if correct) / len(bucket)
        ece += (len(bucket) / total) * abs(avg_conf - acc)
    return ece


def summarize(
    pairs: Sequence[tuple[float, bool]],
    *,
    bucket_count: int = 10,
) -> CalibrationSummary:
    return CalibrationSummary(
        brier=brier_score(pairs),
        ece=expected_calibration_error(pairs, bucket_count=bucket_count),
        bucket_count=bucket_count,
        sample_size=len(pairs),
    )


__all__ = [
    "CalibrationSummary",
    "brier_score",
    "expected_calibration_error",
    "summarize",
]

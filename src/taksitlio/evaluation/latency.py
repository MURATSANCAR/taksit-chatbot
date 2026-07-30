"""Latency percentile helper.

Uses a nearest-rank percentile so results are stable on tiny samples
(smoke evals with ~5 cases). We deliberately avoid numpy so the
evaluation package can be imported without the api extra.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Sequence


@dataclass(frozen=True)
class LatencySummary:
    p50_ms: float
    p95_ms: float
    p99_ms: float
    mean_ms: float
    count: int

    def to_dict(self) -> dict:
        return {
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
            "p99_ms": self.p99_ms,
            "mean_ms": self.mean_ms,
            "count": self.count,
        }


def percentile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if q <= 0:
        return sorted_values[0]
    if q >= 100:
        return sorted_values[-1]
    rank = max(1, ceil(len(sorted_values) * q / 100.0))
    return float(sorted_values[rank - 1])


def summarize(values: Sequence[float]) -> LatencySummary:
    if not values:
        return LatencySummary(0.0, 0.0, 0.0, 0.0, 0)
    ordered = sorted(float(v) for v in values)
    mean = sum(ordered) / len(ordered)
    return LatencySummary(
        p50_ms=percentile(ordered, 50),
        p95_ms=percentile(ordered, 95),
        p99_ms=percentile(ordered, 99),
        mean_ms=mean,
        count=len(ordered),
    )


__all__ = ["LatencySummary", "percentile", "summarize"]

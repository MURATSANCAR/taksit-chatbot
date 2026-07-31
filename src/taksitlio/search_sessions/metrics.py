"""Live search-session latency metrics aggregation (ADR-011 P2)."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence


LATENCY_METRIC_NAMES = (
    "queue_wait_ms",
    "llm_inference_ms",
    "partial_result_latency_ms",
    "search_complete_ms",
    "fast_path_completion_ms",
)


def percentile(values: Sequence[float], p: float) -> Optional[float]:
    if not values:
        return None
    xs = sorted(float(v) for v in values)
    if len(xs) == 1:
        return xs[0]
    rank = (len(xs) - 1) * (p / 100.0)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - rank) + xs[hi] * (rank - lo)


@dataclass
class MetricsRegistry:
    """Process-local observations + optional dual-write to session repos."""

    observations: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    counters: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def observe(self, name: str, value: float, **_labels: Any) -> None:
        self.observations[name].append(float(value))

    def incr(self, name: str, value: int = 1, **_labels: Any) -> None:
        self.counters[name] += int(value)

    def summary(self, names: Iterable[str] = LATENCY_METRIC_NAMES) -> dict[str, Any]:
        out: dict[str, Any] = {"latencies": {}, "counters": dict(self.counters)}
        for name in names:
            vals = self.observations.get(name) or []
            out["latencies"][name] = {
                "count": len(vals),
                "p50": percentile(vals, 50),
                "p95": percentile(vals, 95),
                "p99": percentile(vals, 99),
                "max": max(vals) if vals else None,
            }
        return out

    def ingest_session_metrics(self, rows: Sequence[Mapping[str, Any]]) -> None:
        for row in rows:
            name = str(row.get("metric_name") or "")
            try:
                value = float(row.get("metric_value"))
            except (TypeError, ValueError):
                continue
            if name.endswith("_ms") or name in LATENCY_METRIC_NAMES:
                self.observe(name, value)
            else:
                self.incr(name, int(value) if value == int(value) else 1)


# Module singleton used by API / worker
GLOBAL_SEARCH_METRICS = MetricsRegistry()

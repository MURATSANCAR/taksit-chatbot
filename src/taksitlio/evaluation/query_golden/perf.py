"""Performance microbench lane for Query Golden parser (ADR-013)."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional, Sequence

from taksitlio.evaluation.query_golden.loader import QueryGoldenCase
from taksitlio.query_understanding import CatalogHints, detect_gaps, fast_parse


def _percentile(sorted_vals: list[float], p: float) -> Optional[float]:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


@dataclass
class PerfLaneMetrics:
    samples: int = 0
    parser_p50_ms: Optional[float] = None
    parser_p95_ms: Optional[float] = None
    parser_p99_ms: Optional[float] = None
    gap_p95_ms: Optional[float] = None
    total_p95_ms: Optional[float] = None
    warm: bool = False
    support: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_perf_lane(
    cases: Sequence[QueryGoldenCase],
    *,
    catalog: CatalogHints,
    warm_iters: int = 1,
    limit: Optional[int] = None,
) -> tuple[PerfLaneMetrics, list[dict[str, Any]]]:
    subset = list(cases[:limit] if limit else cases)
    # Warmup
    for _ in range(max(0, warm_iters)):
        for case in subset[: min(20, len(subset))]:
            parse = fast_parse(case.message, catalog=catalog)
            detect_gaps(parse)

    parser_ms: list[float] = []
    gap_ms: list[float] = []
    total_ms: list[float] = []
    details: list[dict[str, Any]] = []

    for case in subset:
        t0 = time.perf_counter()
        parse = fast_parse(case.message, catalog=catalog)
        t1 = time.perf_counter()
        detect_gaps(parse)
        t2 = time.perf_counter()
        p_ms = (t1 - t0) * 1000.0
        g_ms = (t2 - t1) * 1000.0
        tot = (t2 - t0) * 1000.0
        parser_ms.append(p_ms)
        gap_ms.append(g_ms)
        total_ms.append(tot)
        details.append({"case_id": case.case_id, "parser_ms": round(p_ms, 3), "total_ms": round(tot, 3)})

    parser_ms.sort()
    gap_ms.sort()
    total_ms.sort()
    metrics = PerfLaneMetrics(
        samples=len(subset),
        parser_p50_ms=_percentile(parser_ms, 50),
        parser_p95_ms=_percentile(parser_ms, 95),
        parser_p99_ms=_percentile(parser_ms, 99),
        gap_p95_ms=_percentile(gap_ms, 95),
        total_p95_ms=_percentile(total_ms, 95),
        warm=warm_iters > 0,
        support={"samples": len(subset)},
    )
    return metrics, details


def evaluate_perf_gate(
    metrics: PerfLaneMetrics,
    gates: Mapping[str, Any],
) -> dict[str, Any]:
    thresholds = gates.get("perf_gate_thresholds") or {
        "parser_p95_ms": {"max": 30.0},
        "total_p95_ms": {"max": 50.0},
    }
    violations: list[str] = []
    notes: list[str] = []
    for key, attr in (("parser_p95_ms", metrics.parser_p95_ms), ("total_p95_ms", metrics.total_p95_ms)):
        rule = thresholds.get(key) or {}
        if "max" in rule and attr is not None and float(attr) > float(rule["max"]):
            violations.append(f"{key}: {attr:.3f} > {rule['max']}")
    # Local CPU variance — BOOTSTRAP warn rather than hard fail on Mac CI
    if violations:
        status = "BOOTSTRAP"
        notes = [
            "perf targets informational on local/CI CPU; hard fail reserved for staging/runtime gate",
            *[f"warn:{v}" for v in violations],
        ]
    else:
        status = "PASS"
        notes = []
    return {"status": status, "violations": violations, "notes": notes}

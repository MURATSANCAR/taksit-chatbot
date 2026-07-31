"""Latency / concurrency benchmarks for ADR-009 runtime verification."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Optional, Sequence


@dataclass
class LatencyStats:
    count: int
    p50_ms: float
    p90_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    mean_ms: float

    def to_dict(self) -> dict[str, float | int]:
        return {
            "count": self.count,
            "p50_ms": self.p50_ms,
            "p90_ms": self.p90_ms,
            "p95_ms": self.p95_ms,
            "p99_ms": self.p99_ms,
            "max_ms": self.max_ms,
            "mean_ms": self.mean_ms,
        }


def percentile(sorted_values: Sequence[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    k = (len(sorted_values) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_values[int(k)])
    d0 = sorted_values[f] * (c - k)
    d1 = sorted_values[c] * (k - f)
    return float(d0 + d1)


def summarize_latencies(values_ms: Iterable[float]) -> LatencyStats:
    vals = sorted(float(v) for v in values_ms)
    if not vals:
        return LatencyStats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return LatencyStats(
        count=len(vals),
        p50_ms=percentile(vals, 50),
        p90_ms=percentile(vals, 90),
        p95_ms=percentile(vals, 95),
        p99_ms=percentile(vals, 99),
        max_ms=vals[-1],
        mean_ms=sum(vals) / len(vals),
    )


@dataclass
class ConcurrencyBenchmarkResult:
    concurrency: int
    requests: int
    success: int
    schema_failure: int
    timeout: int
    fallback: int
    throughput_rps: float
    tokens_per_sec: Optional[float]
    latency: LatencyStats
    phase: str  # COLD | WARM
    active_queue_depth_max: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "concurrency": self.concurrency,
            "requests": self.requests,
            "success": self.success,
            "schema_failure": self.schema_failure,
            "timeout": self.timeout,
            "fallback": self.fallback,
            "throughput_rps": self.throughput_rps,
            "tokens_per_sec": self.tokens_per_sec,
            "latency": self.latency.to_dict(),
            "phase": self.phase,
            "active_queue_depth_max": self.active_queue_depth_max,
            "notes": list(self.notes),
        }


async def run_concurrency_benchmark(
    coroutines_factory: Callable[[int], Awaitable[tuple[str, float, int]]],
    *,
    concurrency: int,
    requests: int,
    phase: str,
) -> ConcurrencyBenchmarkResult:
    """Run ``requests`` tasks with a simple semaphore of size ``concurrency``.

    Factory(i) → (status, latency_ms, tokens) where status ∈
    {ok, schema_failure, timeout, fallback, error}.
    """

    import asyncio

    sem = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    success = schema_failure = timeout = fallback = 0
    tokens_total = 0
    queue_depth_max = 0
    in_flight = 0

    async def _one(i: int) -> None:
        nonlocal success, schema_failure, timeout, fallback, tokens_total, in_flight, queue_depth_max
        async with sem:
            in_flight += 1
            queue_depth_max = max(queue_depth_max, in_flight)
            try:
                status, latency_ms, tokens = await coroutines_factory(i)
            finally:
                in_flight -= 1
            latencies.append(latency_ms)
            tokens_total += max(0, tokens)
            if status == "ok":
                success += 1
            elif status == "schema_failure":
                schema_failure += 1
            elif status == "timeout":
                timeout += 1
            elif status == "fallback":
                fallback += 1

    started = time.perf_counter()
    await asyncio.gather(*[_one(i) for i in range(requests)])
    elapsed = max(time.perf_counter() - started, 1e-9)
    notes: list[str] = []
    stats = summarize_latencies(latencies)
    if phase == "WARM" and stats.p50_ms >= 2000:
        notes.append(f"FAST warm P50 {stats.p50_ms:.1f}ms ≥ 2000ms target")
    if phase == "WARM" and stats.p95_ms >= 3000:
        notes.append(f"FAST warm P95 {stats.p95_ms:.1f}ms ≥ 3000ms target")
    if schema_failure:
        notes.append(f"schema_failure={schema_failure} (target 0)")

    return ConcurrencyBenchmarkResult(
        concurrency=concurrency,
        requests=requests,
        success=success,
        schema_failure=schema_failure,
        timeout=timeout,
        fallback=fallback,
        throughput_rps=requests / elapsed,
        tokens_per_sec=(tokens_total / elapsed) if tokens_total else None,
        latency=stats,
        phase=phase,
        active_queue_depth_max=queue_depth_max,
        notes=notes,
    )


@dataclass
class PgvectorScaleResult:
    category_count: int
    embedding_dimension: int
    index_type: str
    index_size_bytes: Optional[int]
    insert_duration_ms: float
    index_build_duration_ms: float
    query: LatencyStats
    throughput_qps: float
    candidate_recall: Optional[float]
    database_cpu_percent: Optional[float] = None
    database_memory_bytes: Optional[float] = None
    planner_used_index: Optional[bool] = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category_count": self.category_count,
            "embedding_dimension": self.embedding_dimension,
            "index_type": self.index_type,
            "index_size_bytes": self.index_size_bytes,
            "insert_duration_ms": self.insert_duration_ms,
            "index_build_duration_ms": self.index_build_duration_ms,
            "query": self.query.to_dict(),
            "throughput_qps": self.throughput_qps,
            "candidate_recall": self.candidate_recall,
            "database_cpu_percent": self.database_cpu_percent,
            "database_memory_bytes": self.database_memory_bytes,
            "planner_used_index": self.planner_used_index,
            "notes": list(self.notes),
        }

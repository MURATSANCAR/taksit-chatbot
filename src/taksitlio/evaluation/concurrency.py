"""Concurrency helper for the evaluation runner.

Only exposes a small bounded gather so we can run cases in parallel
without pulling in a task-queue dependency. The runner captures both
per-case latency and queue wait so the observed p95 stays honest even
under concurrency.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Sequence, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class ConcurrencySummary:
    workers: int
    queue_wait_p95_ms: float
    throughput_qps: float
    total_wall_ms: float

    def to_dict(self) -> dict:
        return {
            "workers": self.workers,
            "queue_wait_p95_ms": self.queue_wait_p95_ms,
            "throughput_qps": self.throughput_qps,
            "total_wall_ms": self.total_wall_ms,
        }


async def bounded_gather(
    factories: Sequence[Callable[[], Awaitable[T]]],
    *,
    workers: int = 4,
) -> list[T]:
    """Run coroutines with a bounded worker semaphore."""

    if workers <= 0:
        workers = 1
    semaphore = asyncio.Semaphore(workers)

    async def _wrapped(factory: Callable[[], Awaitable[T]]) -> T:
        async with semaphore:
            return await factory()

    return await asyncio.gather(*(_wrapped(f) for f in factories))


__all__ = ["ConcurrencySummary", "bounded_gather"]

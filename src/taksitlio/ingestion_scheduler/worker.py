"""Scheduler worker lease loop (ADR-010 P7).

Does not crawl synchronously for user requests — processes leased jobs only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Protocol

from taksitlio.ingestion_scheduler.repository import (
    SchedulerJobRecord,
    SchedulerJobRepository,
)

JobHandler = Callable[[SchedulerJobRecord], Awaitable[None]]


class SchedulerWorker(Protocol):
    async def tick(self) -> Optional[SchedulerJobRecord]: ...


@dataclass
class LeaseLoopWorker:
    """Single-tick worker: lease → handle → complete/fail."""

    repo: SchedulerJobRepository
    worker_id: str
    handler: JobHandler
    queue_name: Optional[str] = None
    lease_seconds: int = 60
    retry_delay_seconds: int = 30

    async def tick(self) -> Optional[SchedulerJobRecord]:
        job = await self.repo.lease_next(
            worker_id=self.worker_id,
            queue_name=self.queue_name,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return None
        await self.repo.mark_running(job.id)
        try:
            await self.handler(job)
            await self.repo.complete(job.id)
        except Exception as exc:  # noqa: BLE001 — isolate job faults
            await self.repo.fail(
                job.id,
                error_code=type(exc).__name__,
                error_detail=str(exc)[:2000],
                retry_delay_seconds=self.retry_delay_seconds,
            )
            raise
        return job


async def noop_handler(job: SchedulerJobRecord) -> None:
    """Default handler for wiring tests — real adapters plug in later."""

    _ = job


__all__ = [
    "JobHandler",
    "LeaseLoopWorker",
    "SchedulerWorker",
    "noop_handler",
]

"""Long-running scheduler lease daemon (ADR-010 P8).

Processes background refresh jobs; never blocks chat requests.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from typing import Optional

from taksitlio.ingestion_scheduler.repository import (
    InMemorySchedulerJobRepository,
    PostgresSchedulerJobRepository,
    SchedulerJobRecord,
    SchedulerJobRepository,
)
from taksitlio.ingestion_scheduler.worker import LeaseLoopWorker

logger = logging.getLogger("taksitlio.scheduler_daemon")


async def _default_job_handler(job: SchedulerJobRecord) -> None:
    """Placeholder until queue-specific adapters are wired."""

    logger.info(
        "processed job id=%s queue=%s external=%s payload=%s",
        job.id,
        job.queue_name,
        job.external_item_id,
        dict(job.payload),
    )


async def run_daemon(
    repo: SchedulerJobRepository,
    *,
    worker_id: str,
    poll_interval_seconds: float = 2.0,
    queue_name: Optional[str] = None,
    lease_seconds: int = 60,
    max_ticks: Optional[int] = None,
) -> int:
    """Poll lease loop until cancelled. Returns ticks executed."""

    stop = asyncio.Event()

    def _stop(*_args: object) -> None:
        stop.set()

    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _stop)
            except NotImplementedError:
                pass
    except RuntimeError:
        pass

    worker = LeaseLoopWorker(
        repo=repo,
        worker_id=worker_id,
        handler=_default_job_handler,
        queue_name=queue_name,
        lease_seconds=lease_seconds,
    )
    ticks = 0
    while not stop.is_set():
        if max_ticks is not None and ticks >= max_ticks:
            break
        try:
            job = await worker.tick()
            ticks += 1
            if job is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll_interval_seconds)
                except asyncio.TimeoutError:
                    pass
        except Exception:  # noqa: BLE001
            logger.exception("worker tick failed")
            await asyncio.sleep(poll_interval_seconds)
    return ticks


def build_repo_from_env() -> SchedulerJobRepository:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url and os.environ.get("ALLOW_IN_MEMORY", "").lower() not in {
        "1",
        "true",
        "yes",
    }:
        # Pool created lazily by caller for production; here we keep in-memory
        # unless explicitly constructed with a pool elsewhere.
        logger.warning(
            "DATABASE_URL set but daemon CLI uses in-memory unless --postgres-pool "
            "is provided by the service entrypoint; falling back to in-memory"
        )
    return InMemorySchedulerJobRepository()


async def _amain(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Taksitlio ingestion scheduler daemon")
    parser.add_argument("--worker-id", default=os.environ.get("SCHEDULER_WORKER_ID", "scheduler-1"))
    parser.add_argument("--queue", default=None)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--lease-seconds", type=int, default=60)
    parser.add_argument("--max-ticks", type=int, default=None)
    parser.add_argument("--in-memory", action="store_true", default=True)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    repo: SchedulerJobRepository
    if args.in_memory:
        repo = InMemorySchedulerJobRepository()
    else:
        from taksitlio.db.pool import create_pool

        pool = await create_pool(os.environ["DATABASE_URL"])
        repo = PostgresSchedulerJobRepository(pool)

    ticks = await run_daemon(
        repo,
        worker_id=args.worker_id,
        poll_interval_seconds=args.poll_interval,
        queue_name=args.queue,
        lease_seconds=args.lease_seconds,
        max_ticks=args.max_ticks,
    )
    logger.info("daemon stopped after %s ticks", ticks)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()


__all__ = ["build_repo_from_env", "main", "run_daemon"]

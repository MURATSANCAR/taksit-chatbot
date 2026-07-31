"""Long-running scheduler lease daemon (ADR-010 P8/P9).

Processes background refresh jobs; never blocks chat requests.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from typing import Optional

from taksitlio.ingestion_scheduler.handlers import HandlerContext, QueueDispatchHandler
from taksitlio.ingestion_scheduler.repository import (
    InMemorySchedulerJobRepository,
    PostgresSchedulerJobRepository,
    SchedulerJobRepository,
)
from taksitlio.ingestion_scheduler.worker import LeaseLoopWorker
from taksitlio.media.s3_storage import build_object_storage_from_env
from taksitlio.product.catalog import InMemoryProductCatalogRepository

logger = logging.getLogger("taksitlio.scheduler_daemon")


async def run_daemon(
    repo: SchedulerJobRepository,
    *,
    worker_id: str,
    poll_interval_seconds: float = 2.0,
    queue_name: Optional[str] = None,
    lease_seconds: int = 60,
    max_ticks: Optional[int] = None,
    handler: Optional[QueueDispatchHandler] = None,
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
        handler=handler or QueueDispatchHandler(),
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


def build_handler_from_env(
    *,
    catalog: Optional[object] = None,
    storage_root: Optional[str] = None,
    finance_index: Optional[object] = None,
    campaign_catalog: Optional[object] = None,
    merchant_directory: Optional[object] = None,
    db_pool: Optional[object] = None,
) -> QueueDispatchHandler:
    if storage_root:
        os.environ.setdefault("MEDIA_STORAGE_ROOT", storage_root)
    storage = build_object_storage_from_env()
    return QueueDispatchHandler(
        HandlerContext(
            catalog=catalog or InMemoryProductCatalogRepository(),
            storage=storage,
            finance_index=finance_index,
            campaign_catalog=campaign_catalog,
            merchant_directory=merchant_directory,
            db_pool=db_pool,
        )
    )


async def _amain(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Taksitlio ingestion scheduler daemon")
    parser.add_argument(
        "--worker-id",
        default=os.environ.get("SCHEDULER_WORKER_ID", "scheduler-1"),
    )
    parser.add_argument("--queue", default=None)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--lease-seconds", type=int, default=60)
    parser.add_argument("--max-ticks", type=int, default=None)
    parser.add_argument(
        "--use-postgres",
        action="store_true",
        help="Use DATABASE_URL Postgres scheduler + product catalog",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    catalog: object
    repo: SchedulerJobRepository
    pool = None
    if args.use_postgres:
        from taksitlio.db.pool import create_pool
        from taksitlio.merchant.directory import PostgresMerchantDirectory
        from taksitlio.product.catalog import PostgresProductCatalogRepository
        from taksitlio.product_query.postgres_finance import PostgresFinanceOptionIndex

        pool = await create_pool(os.environ["DATABASE_URL"])
        repo = PostgresSchedulerJobRepository(pool)
        catalog = PostgresProductCatalogRepository(pool)
        handler = build_handler_from_env(
            catalog=catalog,
            finance_index=PostgresFinanceOptionIndex(pool),
            merchant_directory=PostgresMerchantDirectory(pool),
            db_pool=pool,
        )
    else:
        from taksitlio.campaign_catalog.feed_apply import InMemoryCampaignCatalog
        from taksitlio.product_query.finance_index import InMemoryFinanceOptionIndex

        repo = InMemorySchedulerJobRepository()
        catalog = InMemoryProductCatalogRepository()
        handler = build_handler_from_env(
            catalog=catalog,
            finance_index=InMemoryFinanceOptionIndex(),
            campaign_catalog=InMemoryCampaignCatalog(),
        )

    ticks = await run_daemon(
        repo,
        worker_id=args.worker_id,
        poll_interval_seconds=args.poll_interval,
        queue_name=args.queue,
        lease_seconds=args.lease_seconds,
        max_ticks=args.max_ticks,
        handler=handler,
    )
    logger.info("daemon stopped after %s ticks", ticks)
    if pool is not None:
        await pool.close()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()


__all__ = ["build_handler_from_env", "main", "run_daemon"]

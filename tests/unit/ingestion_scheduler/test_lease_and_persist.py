"""P7 — ingestion persist + scheduler lease."""

from __future__ import annotations

import pytest

from taksitlio.ingestion.repository import InMemoryIngestionRepository
from taksitlio.ingestion.store import CreateSourceInput, PersistRunInput
from taksitlio.ingestion_scheduler.domain import (
    PRIORITY_USER_SEARCH_STALE,
    SchedulerJobSpec,
    SchedulerQueue,
)
from taksitlio.ingestion_scheduler.repository import InMemorySchedulerJobRepository
from taksitlio.ingestion_scheduler.worker import LeaseLoopWorker


@pytest.mark.asyncio
async def test_persist_source_and_run() -> None:
    repo = InMemoryIngestionRepository()
    source = await repo.upsert_source(
        CreateSourceInput(
            merchant_id=1,
            source_code="src-a",
            source_type="FEED_JSON",
            adapter_code="generic.json_feed.v1",
            credential_ref="secret://x",
        )
    )
    run = await repo.persist_run(
        PersistRunInput(
            source_id=source.id,
            run_type="FULL",
            status="SUCCEEDED",
            items_discovered=2,
            items_changed=2,
            items=[
                {"external_item_id": "1", "action": "DISCOVERED"},
                {"external_item_id": "2", "action": "DISCOVERED"},
            ],
        )
    )
    assert run.id >= 1
    again = await repo.get_source_by_code("src-a")
    assert again is not None
    assert again.last_success_at is not None
    assert again.consecutive_failures == 0
    assert len(await repo.list_runs(source_id=source.id)) == 1


@pytest.mark.asyncio
async def test_lease_complete_and_retry() -> None:
    repo = InMemorySchedulerJobRepository()
    await repo.enqueue(
        SchedulerJobSpec(
            queue_name=SchedulerQueue.PRICE_REFRESH,
            priority=PRIORITY_USER_SEARCH_STALE,
            product_id="10",
            external_item_id="sku-1",
            payload={"reason": "stale"},
        )
    )

    async def ok(job):  # noqa: ANN001
        assert job.external_item_id == "sku-1"

    worker = LeaseLoopWorker(repo=repo, worker_id="w1", handler=ok)
    leased = await worker.tick()
    assert leased is not None
    jobs = await repo.list_jobs()
    assert jobs[0].status == "SUCCEEDED"
    assert await worker.tick() is None


@pytest.mark.asyncio
async def test_lease_fail_retries() -> None:
    repo = InMemorySchedulerJobRepository()
    await repo.enqueue(
        SchedulerJobSpec(
            queue_name=SchedulerQueue.MEDIA_FETCH,
            priority=50,
            external_item_id="m1",
        )
    )

    async def boom(_job):  # noqa: ANN001
        raise RuntimeError("fetch failed")

    worker = LeaseLoopWorker(
        repo=repo, worker_id="w2", handler=boom, retry_delay_seconds=0
    )
    with pytest.raises(RuntimeError):
        await worker.tick()
    pending = await repo.list_jobs(status="PENDING")
    assert pending
    assert pending[0].attempts == 1
    assert pending[0].error_code == "RuntimeError"

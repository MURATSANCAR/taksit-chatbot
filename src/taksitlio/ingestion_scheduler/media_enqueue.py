"""Enqueue MEDIA_FETCH jobs after catalog upsert (ADR-010 P9).

Source image URLs are worker inputs only — chatbot cards use CDN after ingest.
"""

from __future__ import annotations

from typing import Any, Optional

from taksitlio.ingestion.runner import IngestionRunResult
from taksitlio.ingestion_scheduler.domain import (
    PRIORITY_DEFAULT,
    SchedulerJobSpec,
    SchedulerQueue,
)
from taksitlio.ingestion_scheduler.repository import SchedulerJobRepository
from taksitlio.product.catalog import CatalogApplyResult


async def enqueue_media_jobs_for_applied(
    *,
    ingestion: IngestionRunResult,
    applied: CatalogApplyResult,
    scheduler: SchedulerJobRepository,
    catalog: Any,
    source_id: Optional[int] = None,
) -> int:
    """Set pending source image + enqueue MEDIA_FETCH per applied product."""

    by_ext = {
        i.external_product_id: i.product_id
        for i in applied.items
        if i.product_id is not None
    }
    enqueued = 0
    for row in ingestion.items:
        product_id = by_ext.get(row.external_product_id)
        if product_id is None or not row.media:
            continue
        primary = next(
            (m for m in row.media if m.media_role == "PRIMARY"),
            row.media[0],
        )
        source_url = primary.source_url
        if not source_url:
            continue
        if hasattr(catalog, "set_pending_source_image"):
            await catalog.set_pending_source_image(product_id, source_url)
        await scheduler.enqueue(
            SchedulerJobSpec(
                queue_name=SchedulerQueue.MEDIA_FETCH,
                priority=PRIORITY_DEFAULT,
                source_id=None if source_id is None else str(source_id),
                product_id=str(product_id),
                external_item_id=row.external_product_id,
                payload={
                    "source_url": source_url,
                    "source_reference": primary.source_reference
                    or ingestion.source_code,
                },
            )
        )
        enqueued += 1
    return enqueued


__all__ = ["enqueue_media_jobs_for_applied"]

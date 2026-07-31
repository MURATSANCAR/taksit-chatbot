"""Queue-specific scheduler job handlers (ADR-010 P9).

MEDIA_FETCH downloads merchant source images into object storage and attaches
CDN URLs — chatbot never hotlinks ``source_url``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from taksitlio.ingestion_scheduler.domain import SchedulerQueue
from taksitlio.ingestion_scheduler.repository import SchedulerJobRecord
from taksitlio.media.pipeline import download_image, ingest_image_bytes
from taksitlio.media.storage import ObjectStorage
from taksitlio.media.types import MediaStatus

logger = logging.getLogger("taksitlio.scheduler.handlers")

DownloadFn = Callable[..., Awaitable[bytes]]


@dataclass
class HandlerContext:
    catalog: Any = None  # ProductCatalogRepository with media helpers
    storage: Optional[ObjectStorage] = None
    download_image: DownloadFn = download_image
    finance_index: Any = None
    campaign_catalog: Any = None
    merchant_directory: Any = None
    db_pool: Any = None


class QueueDispatchHandler:
    """Route leased jobs by ``queue_name``."""

    def __init__(self, ctx: Optional[HandlerContext] = None) -> None:
        self.ctx = ctx or HandlerContext()

    async def __call__(self, job: SchedulerJobRecord) -> None:
        queue = job.queue_name
        if queue == SchedulerQueue.MEDIA_FETCH.value:
            await self._handle_media_fetch(job)
            return
        if queue in {
            SchedulerQueue.PRICE_REFRESH.value,
            SchedulerQueue.STOCK_REFRESH.value,
        }:
            await self._handle_price_or_stock(job)
            return
        if queue in {
            SchedulerQueue.CAMPAIGN_REFRESH.value,
            SchedulerQueue.RATE_REFRESH.value,
        }:
            await self._handle_campaign_or_rate(job)
            return
        if queue in {
            SchedulerQueue.PRODUCT_DISCOVERY.value,
            SchedulerQueue.PRODUCT_DETAIL.value,
            SchedulerQueue.FAILED_ITEM_RETRY.value,
        }:
            logger.info(
                "acknowledged queue=%s job_id=%s external=%s",
                queue,
                job.id,
                job.external_item_id,
            )
            return
        logger.warning("unhandled queue=%s job_id=%s", queue, job.id)

    async def _handle_media_fetch(self, job: SchedulerJobRecord) -> None:
        catalog = self.ctx.catalog
        storage = self.ctx.storage
        if catalog is None or storage is None:
            logger.info("media fetch skipped — catalog/storage not configured")
            return
        if job.product_id is None:
            raise ValueError("MEDIA_FETCH requires product_id")

        payload = dict(job.payload or {})
        source_url = payload.get("source_url")
        if not source_url:
            product = await catalog.get_product(int(job.product_id))
            source_url = None if product is None else product.pending_source_image_url
        if not source_url:
            raise ValueError("MEDIA_FETCH missing source_url")

        data = await self.ctx.download_image(str(source_url))
        outcome = ingest_image_bytes(
            data,
            source_url=str(source_url),
            storage=storage,
            source_reference=payload.get("source_reference"),
        )
        draft = outcome.draft
        cdn_url = draft.cdn_url
        if not cdn_url and draft.storage_key:
            cdn_url = storage.cdn_url_for(draft.storage_key)

        # Quarantined images still get CDN of original for ops; chatbot uses
        # IMAGE_UNAVAILABLE when status is not READY (card layer).
        await catalog.attach_primary_media(
            int(job.product_id),
            cdn_url=cdn_url,
            sha256=draft.sha256,
            status=draft.status.value,
            source_url=str(source_url),
            storage_key=draft.storage_key,
            mime_type=draft.mime_type,
            width=draft.width,
            height=draft.height,
            file_size=draft.file_size,
        )
        logger.info(
            "media attached product_id=%s status=%s cdn=%s",
            job.product_id,
            draft.status.value,
            cdn_url,
        )
        if draft.status is not MediaStatus.READY:
            # Job succeeds (ingested) even if quality quarantine — no retry storm.
            logger.warning(
                "media quarantined product_id=%s reasons=%s",
                job.product_id,
                dict(draft.quality_detail),
            )

    async def _handle_price_or_stock(self, job: SchedulerJobRecord) -> None:
        catalog = self.ctx.catalog
        if catalog is None or job.product_id is None:
            logger.info("price/stock refresh ack job_id=%s (no catalog)", job.id)
            return
        payload = dict(job.payload or {})
        price = payload.get("current_price")
        if price is None:
            # Search-driven stale enqueue without new price — mark freshness only.
            await catalog.mark_offer_stale(int(job.product_id))
            logger.info("marked offer stale product_id=%s", job.product_id)
            return
        stock = str(payload.get("stock_status") or "UNKNOWN")
        currency = str(payload.get("currency") or "TRY")
        await catalog.refresh_offer_price(
            int(job.product_id),
            price=float(price),
            currency=currency,
            stock_status=stock,
            list_price=payload.get("list_price"),
        )
        logger.info(
            "offer refreshed product_id=%s price=%s stock=%s",
            job.product_id,
            price,
            stock,
        )
        await self._rebuild_finance_for_job_product(job)

    async def _handle_campaign_or_rate(self, job: SchedulerJobRecord) -> None:
        payload = dict(job.payload or {})
        merchant_codes = payload.get("merchant_codes") or []
        if isinstance(merchant_codes, str):
            merchant_codes = [merchant_codes]
        deps = self._finance_deps()
        if deps is None:
            logger.info(
                "campaign/rate refresh ack job_id=%s (finance deps missing)", job.id
            )
            return
        from taksitlio.product_query.auto_finance import rebuild_after_campaign_feed

        stats = await rebuild_after_campaign_feed(
            deps, merchant_codes=tuple(str(c) for c in merchant_codes)
        )
        logger.info(
            "campaign/rate finance rebuild job_id=%s synced=%s eligible=%s",
            job.id,
            stats.products_synced,
            stats.eligible_options,
        )

    def _finance_deps(self) -> Any:
        if self.ctx.finance_index is None or self.ctx.catalog is None:
            return None
        if self.ctx.campaign_catalog is None and self.ctx.db_pool is None:
            return None
        from taksitlio.product_query.auto_finance import FinanceAutoSyncDeps

        return FinanceAutoSyncDeps(
            finance_index=self.ctx.finance_index,
            product_catalog=self.ctx.catalog,
            merchant_directory=self.ctx.merchant_directory,
            campaign_catalog=self.ctx.campaign_catalog,
            db_pool=self.ctx.db_pool,
        )

    async def _rebuild_finance_for_job_product(self, job: SchedulerJobRecord) -> None:
        deps = self._finance_deps()
        if deps is None or job.product_id is None:
            return
        product = await self.ctx.catalog.get_product(int(job.product_id))
        if product is None:
            return
        from taksitlio.product_query.auto_finance import rebuild_finance_for_product

        await rebuild_finance_for_product(
            deps,
            product_id=int(job.product_id),
            merchant_id=int(product.merchant_id),
        )


__all__ = ["HandlerContext", "QueueDispatchHandler"]

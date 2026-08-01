"""Emit catalog domain events (transactional outbox into catalog_domain_events)."""

from __future__ import annotations

import json
from typing import Any, Optional, Sequence
from uuid import uuid4

from taksitlio.catalog_events.models import CatalogDomainEvent, CatalogEventType


async def insert_domain_event(
    conn: Any,
    event: CatalogDomainEvent,
    *,
    processing_status: str = "PENDING",
) -> Optional[str]:
    """Insert one event; returns event_id or None on idempotent conflict / missing table."""

    event_id = str(uuid4())
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO catalog_domain_events (
              event_id, event_type, source_id, source_revision, source_item_id,
              ingestion_run_id, content_hash, entity_type, entity_id,
              merchant_id, product_id, offer_id, payload, catalog_revision,
              processing_status
            )
            SELECT
              $1::uuid,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14,$15
            WHERE NOT EXISTS (
              SELECT 1 FROM catalog_domain_events
               WHERE source_id IS NOT DISTINCT FROM $3
                 AND source_item_id IS NOT DISTINCT FROM $5
                 AND source_revision IS NOT DISTINCT FROM $4
                 AND content_hash IS NOT DISTINCT FROM $7
                 AND event_type = $2
            )
            RETURNING event_id::text
            """,
            event_id,
            event.event_type.value,
            event.source_id,
            event.source_revision,
            event.source_item_id,
            event.ingestion_run_id,
            event.content_hash,
            event.entity_type,
            event.entity_id,
            event.merchant_id,
            event.product_id,
            event.offer_id,
            json.dumps(dict(event.payload or {}), default=str),
            event.catalog_revision,
            processing_status,
        )
    except Exception:
        return None
    if row is None:
        return None
    return str(row["event_id"])


def _ev(
    event_type: CatalogEventType,
    *,
    merchant_id: int,
    product_id: int,
    external_product_id: str,
    content_hash: str,
    source_id: str,
    catalog_revision: str,
    ingestion_run_id: Optional[str],
    offer_id: Optional[int],
    payload: dict[str, object],
) -> CatalogDomainEvent:
    return CatalogDomainEvent(
        event_type=event_type,
        source_id=source_id,
        source_item_id=external_product_id,
        source_revision=content_hash,
        content_hash=content_hash,
        entity_type="product",
        entity_id=str(product_id),
        merchant_id=merchant_id,
        product_id=product_id,
        offer_id=offer_id,
        catalog_revision=catalog_revision,
        ingestion_run_id=ingestion_run_id,
        payload=payload,
    )


def events_for_product_upsert(
    *,
    merchant_id: int,
    product_id: int,
    external_product_id: str,
    content_hash: Optional[str],
    was_insert: bool,
    source_id: str,
    catalog_revision: str,
    ingestion_run_id: Optional[str] = None,
    offer_id: Optional[int] = None,
    offer_changed: bool = False,
    price_changed: bool = False,
    stock_changed: bool = False,
) -> list[CatalogDomainEvent]:
    """Build organic events for a product/offer mutate (no merchant-name branches)."""

    digest = content_hash or f"{product_id}:{catalog_revision}"
    kw = dict(
        merchant_id=merchant_id,
        product_id=product_id,
        external_product_id=external_product_id,
        source_id=source_id,
        catalog_revision=catalog_revision,
        ingestion_run_id=ingestion_run_id,
        offer_id=offer_id,
    )
    out: list[CatalogDomainEvent] = []
    if was_insert:
        out.append(
            _ev(
                CatalogEventType.PRODUCT_DISCOVERED,
                content_hash=digest,
                payload={"change": "insert"},
                **kw,
            )
        )
    else:
        out.append(
            _ev(
                CatalogEventType.PRODUCT_CHANGED,
                content_hash=digest,
                payload={"change": "update"},
                **kw,
            )
        )
    if offer_changed:
        out.append(
            _ev(
                CatalogEventType.OFFER_CHANGED,
                content_hash=f"{digest}:offer",
                payload={"change": "offer"},
                **kw,
            )
        )
    if price_changed:
        out.append(
            _ev(
                CatalogEventType.PRICE_CHANGED,
                content_hash=f"{digest}:price",
                payload={"change": "price"},
                **kw,
            )
        )
    if stock_changed:
        out.append(
            _ev(
                CatalogEventType.STOCK_CHANGED,
                content_hash=f"{digest}:stock",
                payload={"change": "stock"},
                **kw,
            )
        )
    out.append(
        CatalogDomainEvent(
            event_type=CatalogEventType.MERCHANT_READINESS_RECALCULATION_REQUESTED,
            source_id=source_id,
            source_item_id=f"merchant:{merchant_id}",
            source_revision=catalog_revision,
            content_hash=f"ready-recalc:{merchant_id}:{digest}",
            entity_type="merchant",
            entity_id=str(merchant_id),
            merchant_id=merchant_id,
            product_id=product_id,
            catalog_revision=catalog_revision,
            ingestion_run_id=ingestion_run_id,
            payload={"request": "readiness_recalc"},
        )
    )
    return out


async def emit_events(
    conn: Any,
    events: Sequence[CatalogDomainEvent],
) -> dict[str, int]:
    created = 0
    skipped = 0
    for ev in events:
        eid = await insert_domain_event(conn, ev)
        if eid:
            created += 1
        else:
            skipped += 1
    return {"created": created, "skipped_idempotent": skipped}


__all__ = [
    "emit_events",
    "events_for_product_upsert",
    "insert_domain_event",
]

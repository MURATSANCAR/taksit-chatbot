"""DB rebuild for search_ready_product_projection (INTERNAL / READY merchants only)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def rebuild_search_ready_projection(
    conn: Any,
    *,
    catalog_revision: Optional[str] = None,
    readiness_policy_version: Optional[str] = None,
) -> dict[str, Any]:
    """Fill projection from latest READY readiness snapshots + quality gates.

    Never includes BLOCKED/PARTIAL/DEGRADED merchants.
    """

    rev = catalog_revision or _now()
    # Latest snapshot per merchant
    ready_merchants = await conn.fetch(
        """
        SELECT DISTINCT ON (merchant_id) merchant_id, status, id AS snapshot_id
        FROM merchant_readiness_snapshots
        ORDER BY merchant_id, evaluated_at DESC
        """
    )
    ready_ids = [int(r["merchant_id"]) for r in ready_merchants if r["status"] == "READY"]
    await conn.execute("DELETE FROM search_ready_product_projection")
    if not ready_ids:
        return {
            "catalog_revision": rev,
            "ready_merchants": 0,
            "rows": 0,
            "deleted_all": True,
            "captured_at": _now(),
        }

    rows = await conn.fetch(
        """
        INSERT INTO search_ready_product_projection (
          product_id, offer_id, merchant_id, category_id, brand_id,
          readiness_status, card_media_id, current_price, currency, stock_status,
          checkout_url_present, finance_ready, catalog_revision,
          readiness_policy_version, media_quality_policy_version, updated_at
        )
        SELECT
          p.id,
          o.id,
          p.merchant_id,
          p.category_id,
          p.brand_id,
          'READY',
          (
            SELECT pml.media_asset_id
            FROM product_media_links pml
            JOIN media_assets ma ON ma.id = pml.media_asset_id
            WHERE pml.product_id = p.id AND pml.is_primary AND ma.status = 'READY'
            ORDER BY pml.id LIMIT 1
          ) AS card_media_id,
          o.current_price,
          o.currency,
          o.stock_status,
          (o.checkout_url IS NOT NULL AND length(o.checkout_url) > 5),
          EXISTS (
            SELECT 1 FROM product_finance_options pfo
            WHERE pfo.product_offer_id = o.id AND pfo.eligibility_status = 'ELIGIBLE'
          ),
          $2,
          $3,
          NULL,
          NOW()
        FROM products p
        JOIN product_offers o ON o.product_id = p.id
        WHERE p.status = 'ACTIVE'
          AND p.merchant_id = ANY($1::bigint[])
          AND p.category_id IS NOT NULL
          AND o.current_price IS NOT NULL AND o.current_price > 0
          AND o.freshness_status = 'FRESH'
          AND o.checkout_url IS NOT NULL AND length(o.checkout_url) > 5
          AND EXISTS (
            SELECT 1 FROM product_media_links pml
            JOIN media_assets ma ON ma.id = pml.media_asset_id
            WHERE pml.product_id = p.id AND pml.is_primary AND ma.status = 'READY'
          )
        ON CONFLICT (product_id) DO UPDATE SET
          offer_id = EXCLUDED.offer_id,
          merchant_id = EXCLUDED.merchant_id,
          category_id = EXCLUDED.category_id,
          brand_id = EXCLUDED.brand_id,
          readiness_status = EXCLUDED.readiness_status,
          card_media_id = EXCLUDED.card_media_id,
          current_price = EXCLUDED.current_price,
          currency = EXCLUDED.currency,
          stock_status = EXCLUDED.stock_status,
          checkout_url_present = EXCLUDED.checkout_url_present,
          finance_ready = EXCLUDED.finance_ready,
          catalog_revision = EXCLUDED.catalog_revision,
          readiness_policy_version = EXCLUDED.readiness_policy_version,
          updated_at = NOW()
        RETURNING product_id
        """,
        ready_ids,
        rev,
        readiness_policy_version,
    )

    # Leakage checks
    leak = await conn.fetchrow(
        """
        SELECT
          count(*) FILTER (WHERE readiness_status <> 'READY') AS non_ready_rows,
          count(*) FILTER (WHERE category_id IS NULL) AS unresolved_category,
          count(*) FILTER (WHERE current_price IS NULL OR current_price <= 0) AS invalid_price,
          count(*) FILTER (WHERE NOT checkout_url_present) AS invalid_url,
          count(*) FILTER (WHERE card_media_id IS NULL) AS non_card_ready
        FROM search_ready_product_projection
        """
    )
    blocked_leak = await conn.fetchval(
        """
        SELECT count(*) FROM search_ready_product_projection s
        JOIN (
          SELECT DISTINCT ON (merchant_id) merchant_id, status
          FROM merchant_readiness_snapshots
          ORDER BY merchant_id, evaluated_at DESC
        ) r ON r.merchant_id = s.merchant_id
        WHERE r.status IN ('BLOCKED', 'PARTIAL', 'DEGRADED', 'DISABLED')
        """
    )
    finance_ready = await conn.fetchval(
        "SELECT count(*) FROM search_ready_product_projection WHERE finance_ready"
    )
    return {
        "catalog_revision": rev,
        "ready_merchants": len(ready_ids),
        "ready_merchant_ids": ready_ids,
        "rows": len(rows),
        "finance_ready_rows": int(finance_ready or 0),
        "leakage": {
            "blocked_partial_degraded": int(blocked_leak or 0),
            "non_ready_rows": int(leak["non_ready_rows"] or 0),
            "unresolved_category": int(leak["unresolved_category"] or 0),
            "invalid_price": int(leak["invalid_price"] or 0),
            "invalid_url": int(leak["invalid_url"] or 0),
            "non_card_ready": int(leak["non_card_ready"] or 0),
        },
        "pass_leakage": int(blocked_leak or 0) == 0
        and int(leak["non_ready_rows"] or 0) == 0
        and int(leak["unresolved_category"] or 0) == 0
        and int(leak["invalid_price"] or 0) == 0
        and int(leak["invalid_url"] or 0) == 0
        and int(leak["non_card_ready"] or 0) == 0,
        "captured_at": _now(),
    }


__all__ = ["rebuild_search_ready_projection"]

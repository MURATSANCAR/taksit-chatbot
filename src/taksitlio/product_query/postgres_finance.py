"""Postgres-backed finance option index (ADR-010 P12).

Reads/writes ``product_finance_options`` joined to ``product_offers.product_id``.
"""

from __future__ import annotations

import json
from typing import Any, Optional, Sequence

from taksitlio.product_query.finance_projection import ProductFinanceOptionRow


def _row_from_db(row: Any) -> ProductFinanceOptionRow:
    meta = row["metadata"] if "metadata" in row.keys() else {}
    if meta is None:
        meta = {}
    elif not isinstance(meta, dict):
        meta = dict(meta)
    display_label = meta.get("display_label")
    reasons = meta.get("ineligible_reasons") or []
    if not isinstance(reasons, (list, tuple)):
        reasons = []
    return ProductFinanceOptionRow(
        product_offer_id=str(row["product_offer_id"]),
        merchant_id=str(row["merchant_id"]),
        institution_id=str(row["institution_id"]),
        term_months=int(row["term_months"]),
        monthly_payment=None
        if row["monthly_payment"] is None
        else float(row["monthly_payment"]),
        total_repayment=None
        if row["total_repayment"] is None
        else float(row["total_repayment"]),
        fees_total=float(row["fees_total"] or 0),
        eligibility_status=str(row["eligibility_status"]),
        plan_kind=None if row["plan_kind"] is None else str(row["plan_kind"]),
        freshness_status=str(row["freshness_status"]),
        campaign_id=None if row["campaign_id"] is None else str(row["campaign_id"]),
        rate_snapshot_id=None
        if row["rate_snapshot_id"] is None
        else str(row["rate_snapshot_id"]),
        display_label=None if display_label is None else str(display_label),
        ineligible_reasons=tuple(str(r) for r in reasons),
    )


class PostgresFinanceOptionIndex:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def _offer_id_for_product(self, product_id: int) -> Optional[int]:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                """
                SELECT id FROM product_offers
                WHERE product_id = $1
                ORDER BY id DESC
                LIMIT 1
                """,
                product_id,
            )

    async def list_for_product(
        self, product_id: str
    ) -> Sequence[ProductFinanceOptionRow]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT pfo.*
                FROM product_finance_options pfo
                JOIN product_offers po ON po.id = pfo.product_offer_id
                WHERE po.product_id = $1
                ORDER BY pfo.monthly_payment ASC NULLS LAST, pfo.id ASC
                """,
                int(product_id),
            )
        return tuple(_row_from_db(r) for r in rows)

    async def put(
        self, product_id: str, rows: Sequence[ProductFinanceOptionRow]
    ) -> None:
        pid = int(product_id)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                offer_id = await conn.fetchval(
                    """
                    SELECT id FROM product_offers
                    WHERE product_id = $1
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    pid,
                )
                if offer_id is None:
                    raise ValueError(f"no product_offer for product_id={product_id}")
                await conn.execute(
                    "DELETE FROM product_finance_options WHERE product_offer_id = $1",
                    offer_id,
                )
                for row in rows:
                    meta = {
                        "display_label": row.display_label,
                        "ineligible_reasons": list(row.ineligible_reasons),
                    }
                    await conn.execute(
                        """
                        INSERT INTO product_finance_options (
                            product_offer_id, merchant_id, institution_id,
                            campaign_id, term_months, monthly_payment, total_repayment,
                            fees_total, eligibility_status, plan_kind,
                            rate_snapshot_id, freshness_status, metadata
                        ) VALUES (
                            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb
                        )
                        """,
                        offer_id,
                        int(row.merchant_id),
                        int(row.institution_id),
                        None if row.campaign_id is None else int(row.campaign_id),
                        row.term_months,
                        row.monthly_payment,
                        row.total_repayment,
                        row.fees_total,
                        row.eligibility_status,
                        row.plan_kind,
                        None
                        if row.rate_snapshot_id is None
                        else int(row.rate_snapshot_id),
                        row.freshness_status,
                        json.dumps(meta, ensure_ascii=False),
                    )


class PostgresInstitutionLabelLoader:
    """Load institution_id → display_name (+ logo CDN) from financial_institutions."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def load_labels(self) -> dict[str, str]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, display_name
                FROM financial_institutions
                WHERE status = 'ACTIVE'
                """
            )
        return {str(r["id"]): str(r["display_name"]) for r in rows}

    async def load_logos(self) -> dict[str, str]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (fim.institution_id)
                       fim.institution_id::text AS institution_id,
                       ma.cdn_url
                FROM financial_institution_media fim
                JOIN media_assets ma ON ma.id = fim.media_asset_id
                WHERE ma.status = 'READY'
                  AND ma.cdn_url IS NOT NULL
                  AND fim.role IN ('LOGO', 'PRIMARY', 'ICON')
                  AND (fim.valid_until IS NULL OR fim.valid_until > NOW())
                ORDER BY fim.institution_id,
                         CASE fim.role WHEN 'LOGO' THEN 0 WHEN 'PRIMARY' THEN 1 ELSE 2 END,
                         fim.is_primary DESC
                """
            )
        return {str(r["institution_id"]): str(r["cdn_url"]) for r in rows}


__all__ = [
    "PostgresFinanceOptionIndex",
    "PostgresInstitutionLabelLoader",
]

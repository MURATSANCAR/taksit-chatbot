#!/usr/bin/env python3
"""Project product_finance_options from published campaign terms (ADR-010).

Reads finance_campaigns + campaign_merchants + metadata.terms, ensures
financial_products / rate snapshots / merchant agreements, then rebuilds
eligible installment rows for matching offers. Never invents rates.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _as_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _meta_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _usable_terms(
    *,
    display_name: str,
    campaign_type: str,
    terms: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize published terms into calculable rate rows."""

    name_l = (display_name or "").casefold()
    pesin = "peşin fiyatına" in name_l or "pesin fiyatina" in name_l
    out: list[dict[str, Any]] = []
    for t in terms:
        if not isinstance(t, dict):
            continue
        months = _as_int(t.get("months") if "months" in t else t.get("term_months"))
        if months is None or months <= 0:
            continue
        monthly_pct = _as_float(t.get("monthly_rate_pct"))
        rate_apr = _as_float(t.get("rate_apr"))
        is_zero = (
            campaign_type == "ZERO_RATE"
            or rate_apr == 0.0
            or monthly_pct == 0.0
            or (pesin and monthly_pct is None and rate_apr is None)
        )
        if is_zero:
            out.append(
                {
                    "months": months,
                    "rate_type": "ZERO_RATE",
                    "monthly_rate": 0.0,
                }
            )
            continue
        if monthly_pct is None:
            continue
        out.append(
            {
                "months": months,
                "rate_type": "INTEREST",
                "monthly_rate": monthly_pct / 100.0,
                "annual_cost_rate": (rate_apr / 100.0) if rate_apr is not None else None,
            }
        )
    return out


async def ensure_financial_product(conn: Any, *, institution_id: int, code: str, name: str) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO financial_products (
          institution_id, product_code, display_name, product_type, status
        ) VALUES ($1, $2, $3, 'INSTALLMENT', 'ACTIVE')
        ON CONFLICT (institution_id, product_code) DO UPDATE SET
          display_name = EXCLUDED.display_name,
          status = 'ACTIVE',
          updated_at = NOW()
        RETURNING id
        """,
        institution_id,
        code,
        name,
    )
    return int(row["id"])


async def main() -> None:
    import asyncpg

    from taksitlio.campaign_catalog.models import (
        CampaignStatus,
        CampaignType,
        FinanceCampaignRecord,
        RateSnapshotRecord,
        RateType,
        VerificationStatus,
    )
    from taksitlio.product_query.finance_projection import (
        InstitutionTermOption,
        OfferFinanceContext,
        rebuild_finance_options,
    )
    from taksitlio.product_query.postgres_finance import PostgresFinanceOptionIndex

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL required")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    assert pool is not None
    index = PostgresFinanceOptionIndex(pool)
    stats = {
        "campaigns_considered": 0,
        "campaigns_with_rates": 0,
        "rate_snapshots": 0,
        "agreements": 0,
        "products_projected": 0,
        "eligible_options": 0,
        "skipped_no_rate": 0,
    }

    try:
        async with pool.acquire() as conn:
            camps = await conn.fetch(
                """
                SELECT c.id, c.campaign_code, c.display_name, c.campaign_type,
                       c.status, c.minimum_purchase_amount, c.maximum_purchase_amount,
                       c.metadata, c.source_reference,
                       fi.id AS institution_id, fi.institution_code, fi.display_name AS institution_name
                FROM finance_campaigns c
                JOIN financial_institutions fi ON fi.id = c.institution_id
                ORDER BY c.id
                """
            )

            for camp in camps:
                stats["campaigns_considered"] += 1
                meta = _meta_dict(camp["metadata"])
                raw_terms = meta.get("terms") or []
                if not isinstance(raw_terms, list):
                    raw_terms = []
                # Fallback: campaign_terms months only (may become peşin zero)
                if not raw_terms:
                    term_rows = await conn.fetch(
                        """
                        SELECT term_months AS months
                        FROM campaign_terms
                        WHERE campaign_id = $1 AND included
                        ORDER BY term_months
                        """,
                        camp["id"],
                    )
                    raw_terms = [{"months": int(r["months"])} for r in term_rows]

                usable = _usable_terms(
                    display_name=str(camp["display_name"]),
                    campaign_type=str(camp["campaign_type"]),
                    terms=list(raw_terms),
                )
                if not usable:
                    stats["skipped_no_rate"] += 1
                    continue

                stats["campaigns_with_rates"] += 1
                merchants = await conn.fetch(
                    """
                    SELECT m.id, m.merchant_code
                    FROM campaign_merchants cm
                    JOIN merchants m ON m.id = cm.merchant_id
                    WHERE cm.campaign_id = $1
                    """,
                    camp["id"],
                )
                if not merchants:
                    continue

                product_id = await ensure_financial_product(
                    conn,
                    institution_id=int(camp["institution_id"]),
                    code=f"{camp['institution_code']}-installment",
                    name=f"{camp['institution_name']} Alışveriş Kredisi",
                )

                await conn.execute(
                    """
                    UPDATE finance_campaigns
                    SET status = 'ACTIVE',
                        financial_product_id = $2,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    camp["id"],
                    product_id,
                )

                # Replace prior snapshots for this campaign (idempotent rebuild).
                await conn.execute(
                    "DELETE FROM finance_rate_snapshots WHERE campaign_id = $1",
                    camp["id"],
                )

                # One rate snapshot per term (FRESH from published source).
                snap_ids: dict[int, int] = {}
                for term in usable:
                    months = int(term["months"])
                    rate_type = str(term["rate_type"])
                    snap = await conn.fetchrow(
                        """
                        INSERT INTO finance_rate_snapshots (
                          financial_product_id, campaign_id, minimum_term, maximum_term,
                          monthly_rate, annual_cost_rate, rate_type,
                          verification_status, freshness_status, source_reference, metadata
                        ) VALUES (
                          $1, $2, $3, $3, $4, $5, $6,
                          'UNVERIFIED', 'FRESH', $7, $8::jsonb
                        )
                        RETURNING id
                        """,
                        product_id,
                        camp["id"],
                        months,
                        term.get("monthly_rate"),
                        term.get("annual_cost_rate"),
                        rate_type,
                        camp["source_reference"] or meta.get("source_url"),
                        json.dumps(
                            {
                                "campaign_code": camp["campaign_code"],
                                "term_months": months,
                            },
                            ensure_ascii=False,
                        ),
                    )
                    snap_ids[months] = int(snap["id"])
                    stats["rate_snapshots"] += 1

                merchant_codes = tuple(str(m["merchant_code"]) for m in merchants)
                try:
                    ctype = CampaignType(str(camp["campaign_type"]))
                except ValueError:
                    ctype = CampaignType.INSTALLMENT

                campaign_rec = FinanceCampaignRecord(
                    campaign_code=str(camp["campaign_code"]),
                    institution_code=str(camp["institution_code"]),
                    display_name=str(camp["display_name"]),
                    campaign_type=ctype,
                    status=CampaignStatus.ACTIVE,
                    verification_status=VerificationStatus.UNVERIFIED,
                    minimum_purchase_amount=(
                        float(camp["minimum_purchase_amount"])
                        if camp["minimum_purchase_amount"] is not None
                        else None
                    ),
                    maximum_purchase_amount=(
                        float(camp["maximum_purchase_amount"])
                        if camp["maximum_purchase_amount"] is not None
                        else None
                    ),
                    eligible_terms=tuple(int(t["months"]) for t in usable),
                    eligible_merchant_codes=merchant_codes,
                    agreement_active=True,
                    source_reference=camp["source_reference"],
                )

                for merch in merchants:
                    await conn.execute(
                        """
                        INSERT INTO merchant_financial_agreements (
                          merchant_id, institution_id, financial_product_id, status,
                          source_reference
                        ) VALUES ($1, $2, $3, 'ACTIVE', $4)
                        ON CONFLICT (merchant_id, institution_id, financial_product_id)
                        DO UPDATE SET status = 'ACTIVE', updated_at = NOW()
                        """,
                        merch["id"],
                        camp["institution_id"],
                        product_id,
                        camp["source_reference"] or meta.get("source_url"),
                    )
                    stats["agreements"] += 1

                    offers = await conn.fetch(
                        """
                        SELECT po.id AS offer_id, po.product_id, po.current_price,
                               po.stock_status, po.freshness_status
                        FROM product_offers po
                        WHERE po.merchant_id = $1
                          AND po.freshness_status = 'FRESH'
                          AND po.current_price IS NOT NULL
                          AND po.current_price > 0
                        """,
                        merch["id"],
                    )

                    for offer in offers:
                        term_options: list[InstitutionTermOption] = []
                        for term in usable:
                            months = int(term["months"])
                            rate_type = RateType(str(term["rate_type"]))
                            monthly = term.get("monthly_rate")
                            snap = RateSnapshotRecord(
                                financial_product_code=f"{camp['institution_code']}-installment",
                                rate_type=rate_type,
                                monthly_rate=monthly,
                                annual_cost_rate=term.get("annual_cost_rate"),
                                minimum_term=months,
                                maximum_term=months,
                                term_rates={months: float(monthly or 0.0)},
                                freshness_status="FRESH",
                                verification_status=VerificationStatus.UNVERIFIED,
                                source_reference=camp["source_reference"],
                                campaign_code=str(camp["campaign_code"]),
                            )
                            term_options.append(
                                InstitutionTermOption(
                                    institution_id=str(camp["institution_id"]),
                                    financial_product_code=snap.financial_product_code,
                                    term_months=months,
                                    rate_snapshot=snap,
                                    campaign=campaign_rec,
                                    rate_snapshot_id=str(snap_ids[months]),
                                    campaign_id=str(camp["id"]),
                                )
                            )

                        ctx = OfferFinanceContext(
                            product_offer_id=str(offer["offer_id"]),
                            merchant_id=str(merch["id"]),
                            merchant_code=str(merch["merchant_code"]),
                            purchase_price=float(offer["current_price"]),
                            stock_status=str(offer["stock_status"] or "UNKNOWN"),
                            price_freshness=str(offer["freshness_status"] or "FRESH"),
                            category_id=None,
                        )
                        rows = rebuild_finance_options(ctx, term_options)
                        await index.put(str(offer["product_id"]), rows)
                        stats["products_projected"] += 1
                        stats["eligible_options"] += sum(
                            1 for r in rows if r.eligibility_status == "ELIGIBLE"
                        )

        print(json.dumps(stats, ensure_ascii=False, indent=2))
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

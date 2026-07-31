#!/usr/bin/env python3
"""Upsert live feeds into Postgres (ADR-010 products/offers + finance_campaigns).

Run on a host with DATABASE_URL and applied V015–V018 migrations.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LIVE = Path(os.environ.get("LIVE_FEED_DIR", str(ROOT / "crawler" / "feeds" / "live")))


async def ensure_merchant(conn, *, code: str, name: str) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO merchants (merchant_code, display_name, status)
        VALUES ($1, $2, 'ACTIVE')
        ON CONFLICT (merchant_code) DO UPDATE
          SET display_name = EXCLUDED.display_name,
              status = 'ACTIVE',
              updated_at = NOW()
        RETURNING id
        """,
        code,
        name,
    )
    # V004 merchants may not have updated_at / status — fallback
    if row is None:
        row = await conn.fetchrow(
            "SELECT id FROM merchants WHERE merchant_code=$1", code
        )
    return int(row["id"])


async def ensure_institution(conn, *, code: str, name: str) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO financial_institutions (
          institution_code, display_name, normalized_name, status
        )
        VALUES ($1::text, $2::text, lower($2::text), 'ACTIVE')
        ON CONFLICT (institution_code) DO UPDATE
          SET display_name = EXCLUDED.display_name,
              status = 'ACTIVE',
              updated_at = NOW()
        RETURNING id
        """,
        code,
        name,
    )
    return int(row["id"])


async def ingest_products(pool) -> list[dict[str, Any]]:
    from taksitlio.ingestion.binding import SourceBinding
    from taksitlio.ingestion.runner import run_ingestion_dry
    from taksitlio.product.catalog import (
        PostgresProductCatalogRepository,
        apply_ingestion_to_catalog,
    )

    # Opaque codes → display names from ops registry (not app hardcode maps).
    name_by_code = {
        "src-m-vatan": ("m-vatan", "Vatan Bilgisayar"),
        "src-m-mediamarkt": ("m-mediamarkt", "MediaMarkt"),
        "src-m-koctas": ("m-koctas", "Koçtaş"),
        "src-m-dr": ("m-dr", "D&R"),
    }
    reports = []
    catalog = PostgresProductCatalogRepository(pool)
    async with pool.acquire() as conn:
        for path in sorted(LIVE.glob("src-m-*.json")):
            meta = name_by_code.get(path.stem)
            if meta is None:
                # Derive opaque code from stem: src-m-foo -> m-foo
                code = path.stem.replace("src-", "", 1)
                meta = (code, code)
            code, name = meta
            merchant_id = await ensure_merchant(conn, code=code, name=name)
            binding = SourceBinding(
                source_code=path.stem,
                adapter_code="generic.json_feed.v1",
                merchant_id=str(merchant_id),
                config={"feed_path": str(path)},
            )
            result = await run_ingestion_dry(binding, limit=2000)
            applied = await apply_ingestion_to_catalog(
                result, merchant_id=merchant_id, catalog=catalog
            )
            reports.append(
                {
                    "file": path.name,
                    "merchant_id": merchant_id,
                    "discovered": result.discovered,
                    "chatbot_visible": result.chatbot_visible,
                    "upserted_products": applied.upserted_products,
                    "upserted_offers": applied.upserted_offers,
                }
            )
    return reports


async def ingest_campaigns(pool) -> list[dict[str, Any]]:
    from taksitlio.ingestion.adapters.generic_campaign_feed import (
        GenericCampaignFeedAdapter,
        run_campaign_feed_dry,
    )

    reports = []
    files = {
        "src-b-isbank.json": ("fi-isbank", "İş Bankası"),
        "src-b-fibabanka.json": ("fi-fibabanka", "Fibabanka"),
    }
    async with pool.acquire() as conn:
        for fname, (icode, iname) in files.items():
            path = LIVE / fname
            if not path.exists():
                continue
            institution_id = await ensure_institution(conn, code=icode, name=iname)
            adapter = GenericCampaignFeedAdapter(
                feed_path=path, default_institution_code=icode
            )
            result = await run_campaign_feed_dry(adapter)
            applied = 0
            for camp in result.campaigns:
                await conn.execute(
                    """
                    INSERT INTO finance_campaigns (
                      institution_id, campaign_code, display_name, summary,
                      campaign_type, status, verification_status,
                      minimum_purchase_amount, maximum_purchase_amount,
                      source_reference
                    ) VALUES (
                      $1, $2, $3, $4, $5, 'DRAFT', 'UNVERIFIED', $6, $7, $8
                    )
                    ON CONFLICT (campaign_code) DO UPDATE SET
                      display_name = EXCLUDED.display_name,
                      summary = EXCLUDED.summary,
                      source_reference = EXCLUDED.source_reference,
                      updated_at = NOW()
                    """,
                    institution_id,
                    camp.campaign_code,
                    camp.display_name,
                    None,
                    camp.campaign_type.value,
                    camp.minimum_purchase_amount,
                    camp.maximum_purchase_amount,
                    camp.source_reference,
                )
                # store eligible terms if present
                crow = await conn.fetchrow(
                    "SELECT id FROM finance_campaigns WHERE campaign_code=$1",
                    camp.campaign_code,
                )
                cid = int(crow["id"])
                for months in camp.eligible_terms:
                    await conn.execute(
                        """
                        INSERT INTO campaign_terms (campaign_id, term_months, included)
                        VALUES ($1, $2, TRUE)
                        ON CONFLICT (campaign_id, term_months) DO NOTHING
                        """,
                        cid,
                        months,
                    )
                applied += 1
            reports.append(
                {
                    "file": fname,
                    "institution_id": institution_id,
                    "campaigns_applied": applied,
                    "rates_in_feed": len(result.rates),
                }
            )
    return reports


async def main() -> None:
    import asyncpg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL required")
    # asyncpg wants postgresql:// not sqlalchemy style
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    try:
        product_reports = await ingest_products(pool)
        campaign_reports = await ingest_campaigns(pool)
        async with pool.acquire() as conn:
            counts = {
                "merchants": await conn.fetchval("SELECT count(*) FROM merchants"),
                "products": await conn.fetchval("SELECT count(*) FROM products"),
                "offers": await conn.fetchval("SELECT count(*) FROM product_offers"),
                "institutions": await conn.fetchval(
                    "SELECT count(*) FROM financial_institutions"
                ),
                "campaigns": await conn.fetchval("SELECT count(*) FROM finance_campaigns"),
            }
        print(
            json.dumps(
                {
                    "product_reports": product_reports,
                    "campaign_reports": campaign_reports,
                    "counts": counts,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

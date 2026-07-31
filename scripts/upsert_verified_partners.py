#!/usr/bin/env python3
"""Upsert publicly verified partner merchants/institutions into Postgres.

Does not invent products. Used so fuzzy resolution has real display names
while product feeds catch up merchant-by-merchant.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
REG = ROOT / "crawler" / "ops" / "verified-partners-public.yaml"


async def main() -> None:
    import asyncpg

    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    data = yaml.safe_load(REG.read_text(encoding="utf-8"))
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    merchants = data.get("merchants_verified_public") or []
    banks = data.get("banks_verified_public") or []
    async with pool.acquire() as conn:
        m_ok = 0
        for m in merchants:
            await conn.execute(
                """
                INSERT INTO merchants (merchant_code, display_name, status)
                VALUES ($1::text, $2::text, 'ACTIVE')
                ON CONFLICT (merchant_code) DO UPDATE
                  SET display_name = EXCLUDED.display_name,
                      status = 'ACTIVE',
                      updated_at = NOW()
                """,
                m["merchant_code"],
                m["display_name"],
            )
            m_ok += 1
        b_ok = 0
        for b in banks:
            await conn.execute(
                """
                INSERT INTO financial_institutions (
                  institution_code, display_name, normalized_name, status
                ) VALUES ($1::text, $2::text, lower($2::text), 'ACTIVE')
                ON CONFLICT (institution_code) DO UPDATE
                  SET display_name = EXCLUDED.display_name,
                      status = 'ACTIVE',
                      updated_at = NOW()
                """,
                b["institution_code"],
                b["display_name"],
            )
            b_ok += 1
        counts = {
            "merchants_upserted": m_ok,
            "banks_upserted": b_ok,
            "merchants_total": await conn.fetchval("SELECT count(*) FROM merchants"),
            "institutions_total": await conn.fetchval(
                "SELECT count(*) FROM financial_institutions"
            ),
            "products_total": await conn.fetchval("SELECT count(*) FROM products"),
        }
    await pool.close()
    print(json.dumps(counts, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())

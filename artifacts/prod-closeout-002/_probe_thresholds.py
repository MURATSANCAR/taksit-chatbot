#!/usr/bin/env python3
"""Read readiness thresholds + brand lexicon size."""
from __future__ import annotations

import asyncio
import json
import os
import sys


async def main() -> None:
    import asyncpg

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        thr = await conn.fetch(
            """
            SELECT policy_code, version, status, thresholds
            FROM merchant_readiness_policies p
            JOIN merchant_readiness_policy_versions v ON v.policy_id = p.id
            ORDER BY p.policy_code, v.version DESC
            """
        ) if await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE tablename='merchant_readiness_policies')"
        ) else []
        out = {"thresholds": [dict(r) for r in thr]}
        # fallback: latest snapshot reasons
        snap = await conn.fetch(
            """
            SELECT DISTINCT ON (m.id)
              m.display_name, r.status, r.category_coverage, r.brand_coverage,
              r.attribute_coverage, r.card_media_coverage, r.fresh_price_coverage,
              r.finance_coverage, r.active_products, r.block_reasons, r.thresholds
            FROM merchants m
            JOIN merchant_readiness_snapshots r ON r.merchant_id = m.id
            WHERE m.id = ANY($1::bigint[])
            ORDER BY m.id, r.evaluated_at DESC
            """,
            [8, 11, 20, 40, 5, 18],
        )
        # columns may differ
        cols = [
            c["column_name"]
            for c in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='merchant_readiness_snapshots'"
            )
        ]
        out["snapshot_cols"] = cols
        brands = await conn.fetch(
            "SELECT id, brand_code, display_name FROM brands WHERE status='ACTIVE' ORDER BY id LIMIT 200"
        )
        out["brands_sample"] = [dict(b) for b in brands]
        out["brand_count"] = int(await conn.fetchval("SELECT COUNT(*) FROM brands WHERE status='ACTIVE'") or 0)
        # get snapshots with available cols
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (merchant_id) *
            FROM merchant_readiness_snapshots
            WHERE merchant_id = ANY($1::bigint[])
            ORDER BY merchant_id, evaluated_at DESC
            """,
            [8, 11, 20, 40, 5, 18],
        )
        out["snapshots"] = []
        for r in rows:
            d = dict(r)
            # trim large json
            for k in list(d):
                if isinstance(d[k], (dict, list)) and k not in ("block_reasons", "thresholds", "metrics"):
                    continue
            out["snapshots"].append({k: d[k] for k in d if k in cols})
        # how many products match known brand prefix in display_name for MM/Teknosa
        brand_names = [
            r["display_name"]
            for r in await conn.fetch(
                "SELECT display_name FROM brands WHERE status='ACTIVE' AND length(display_name) >= 3"
            )
        ]
        # count MediaMarkt title brand hits
        for mid in (8, 11):
            hits = 0
            rows_p = await conn.fetch(
                "SELECT display_name FROM products p JOIN product_offers o ON o.product_id=p.id "
                "WHERE o.merchant_id=$1 AND p.status='ACTIVE' AND p.brand_id IS NULL",
                mid,
            )
            upper_brands = sorted(brand_names, key=len, reverse=True)
            for p in rows_p:
                dn = (p["display_name"] or "").strip()
                dn_up = dn.upper()
                for b in upper_brands:
                    bu = b.upper()
                    if dn_up.startswith(bu + " ") or dn_up.startswith(bu + "/") or dn_up == bu:
                        hits += 1
                        break
                    # also first token match for ALLCAPS brands like SAMSUNG
                    first = dn.split()[0].upper() if dn.split() else ""
                    if first == bu or first.rstrip(",") == bu:
                        hits += 1
                        break
            out[f"title_brand_prefix_hits_{mid}"] = hits
            out[f"null_brand_total_{mid}"] = len(rows_p)
        json.dump(out, sys.stdout, indent=2, ensure_ascii=False, default=str)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

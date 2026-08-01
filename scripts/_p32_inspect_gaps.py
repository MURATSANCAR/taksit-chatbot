#!/usr/bin/env python3
"""Inspect merchant readiness gaps (P3.2)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


async def main() -> None:
    import asyncpg
    from taksitlio.merchant_readiness import (
        MerchantCoverageMetrics,
        ReadinessThresholds,
        evaluate_merchant_readiness,
    )

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    thr_row = await conn.fetchval(
        """
        SELECT thresholds FROM merchant_readiness_policy_versions
        WHERE status='ACTIVE' ORDER BY version DESC LIMIT 1
        """
    )
    if isinstance(thr_row, str):
        thr_row = json.loads(thr_row)
    thr = ReadinessThresholds.from_mapping(thr_row or {})
    rows = await conn.fetch(
        """
        SELECT m.id, m.merchant_code, count(*)::bigint AS n,
          count(*) FILTER (WHERE p.category_id IS NOT NULL) AS cat,
          count(*) FILTER (WHERE p.brand_id IS NOT NULL) AS brand,
          count(*) FILTER (WHERE p.attributes IS NOT NULL
            AND p.attributes::text NOT IN ('{}','null')) AS attrs,
          count(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM product_media_links pml
            JOIN media_assets ma ON ma.id=pml.media_asset_id
            WHERE pml.product_id=p.id AND pml.is_primary AND ma.status='READY'
          )) AS media,
          count(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM product_offers o WHERE o.product_id=p.id
              AND o.freshness_status='FRESH'
          )) AS fresh,
          count(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM product_offers o WHERE o.product_id=p.id
              AND o.checkout_url IS NOT NULL AND length(o.checkout_url)>5
          )) AS url
        FROM products p
        JOIN merchants m ON m.id=p.merchant_id
        WHERE p.status='ACTIVE'
        GROUP BY m.id, m.merchant_code
        ORDER BY n DESC
        """
    )
    print(
        f"{'code':20} {'n':>7} {'cat%':>6} {'brand%':>7} {'media%':>7} "
        f"{'fresh%':>7} {'url%':>6} {'status':8} gaps"
    )
    for r in rows:
        n = int(r["n"])
        if n < 20:
            continue
        m = MerchantCoverageMetrics(
            active_products=n,
            searchable_products=n,
            category_coverage=r["cat"] / n,
            brand_coverage=r["brand"] / n,
            attribute_coverage=r["attrs"] / n,
            stock_coverage=1.0,
            card_media_coverage=r["media"] / n,
            fresh_price_coverage=r["fresh"] / n,
            valid_url_coverage=r["url"] / n,
            finance_coverage=0.0,
            payment_plan_coverage=0.0,
        )
        d = evaluate_merchant_readiness(m, thr)
        gaps = []
        checks = [
            ("cat", m.category_coverage, thr.minimum_category_coverage),
            ("brand", m.brand_coverage, thr.minimum_brand_coverage),
            ("attr", m.attribute_coverage, thr.minimum_critical_attribute_coverage),
            ("media", m.card_media_coverage, thr.minimum_card_media_coverage),
            ("fresh", m.fresh_price_coverage, thr.minimum_fresh_price_coverage),
            ("url", m.valid_url_coverage, thr.minimum_valid_url_coverage),
        ]
        for name, actual, req in checks:
            if actual < req:
                gaps.append(f"{name}:{int(round((req - actual) * n))}")
        if n < thr.minimum_searchable_products:
            gaps.append(f"min_n:{thr.minimum_searchable_products - n}")
        print(
            f"{r['merchant_code']:20} {n:7d} {100*m.category_coverage:5.1f} "
            f"{100*m.brand_coverage:6.1f} {100*m.card_media_coverage:6.1f} "
            f"{100*m.fresh_price_coverage:6.1f} {100*m.valid_url_coverage:5.1f} "
            f"{d.status.value:8} {gaps[:5]}"
        )

    # Feed source availability for top merchants
    feed_dir = Path(os.environ.get("LIVE_FEED_DIR") or ROOT / "crawler" / "feeds" / "live")
    print("\nFEED SOURCE AVAILABILITY")
    for path in sorted(feed_dir.glob("src-m-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        prods = data.get("products") or []
        if not prods:
            continue
        sample = prods[: min(2000, len(prods))]
        cat = sum(1 for p in sample if p.get("category") or p.get("category_name"))
        brand = sum(1 for p in sample if p.get("brand"))
        img = sum(1 for p in sample if p.get("image_url") or p.get("image"))
        attrs_cat = sum(
            1
            for p in sample
            if isinstance(p.get("attributes"), dict)
            and (p["attributes"].get("category") or p["attributes"].get("category_name"))
        )
        code = path.name.replace("src-m-", "").replace(".json", "")
        print(
            f"{code:16} n={len(prods):6d} cat_field={cat}/{len(sample)} "
            f"brand={brand}/{len(sample)} img={img}/{len(sample)} attrs_cat={attrs_cat}"
        )
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

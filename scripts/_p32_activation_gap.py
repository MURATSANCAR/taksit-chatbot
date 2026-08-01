#!/usr/bin/env python3
"""P3.2 — merchant activation gap + source availability (no invented categories)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _pct(n: int, d: int) -> float:
    return round(n / max(d, 1), 4)


_GENERIC_URL_ROOTS = {
    "urun",
    "product",
    "products",
    "p",
    "tr",
    "en",
    "cdn",
    "img",
    "images",
}


def classify_source(feed_stats: dict[str, Any], field: str) -> str:
    total = max(int(feed_stats.get("sample") or 0), 1)
    if field == "category":
        hit = int(feed_stats.get("cat_field") or 0) + int(feed_stats.get("attrs_cat") or 0)
        tax_ratio = float(feed_stats.get("url_taxonomy_coverage") or 0.0)
        if tax_ratio >= 0.5:
            return "SOURCE_AVAILABLE_IN_TAXONOMY"
        if tax_ratio >= 0.05 and hit == 0:
            return "SOURCE_AVAILABLE_IN_TAXONOMY"
    elif field == "brand":
        hit = int(feed_stats.get("brand") or 0) + int(feed_stats.get("attrs_brand") or 0)
    elif field == "media":
        hit = int(feed_stats.get("img") or 0)
    else:
        hit = 0
    ratio = hit / total
    if ratio >= 0.5:
        return "SOURCE_AVAILABLE"
    if ratio >= 0.05:
        return "SOURCE_AVAILABLE_IN_RAW_PAYLOAD"
    if hit > 0:
        return "SOURCE_AVAILABLE_IN_ATTRIBUTES"
    return "SOURCE_NOT_AVAILABLE"


async def amain() -> dict[str, Any]:
    import asyncpg
    from taksitlio.merchant_readiness import (
        MerchantCoverageMetrics,
        ReadinessThresholds,
        evaluate_merchant_readiness,
    )
    from taksitlio.merchant_readiness.priority import (
        MerchantPrioritySignals,
        MerchantPriorityWeights,
        top_priority_merchants,
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

    feed_dir = Path(os.environ.get("LIVE_FEED_DIR") or ROOT / "crawler" / "feeds" / "live")
    feed_by_code: dict[str, dict[str, Any]] = {}
    for path in sorted(feed_dir.glob("src-m-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        prods = data.get("products") or []
        if not prods:
            continue
        sample = prods[: min(3000, len(prods))]
        code = "m-" + path.name.replace("src-m-", "").replace(".json", "")
        from collections import Counter
        from urllib.parse import urlparse

        url_roots: Counter[str] = Counter()
        for p in sample:
            raw = str(p.get("url") or p.get("product_url") or p.get("link") or "")
            try:
                parts = [x for x in urlparse(raw).path.split("/") if x]
            except Exception:
                parts = []
            if not parts:
                continue
            root = parts[0].lower()
            if root in _GENERIC_URL_ROOTS or len(root) > 48 or "-p-" in root:
                continue
            url_roots[root] += 1
        top_n = sum(c for _, c in url_roots.most_common(5))
        feed_by_code[code] = {
            "feed_n": len(prods),
            "sample": len(sample),
            "cat_field": sum(
                1 for p in sample if p.get("category") or p.get("category_name")
            ),
            "brand": sum(1 for p in sample if p.get("brand")),
            "img": sum(1 for p in sample if p.get("image_url") or p.get("image")),
            "attrs_cat": sum(
                1
                for p in sample
                if isinstance(p.get("attributes"), dict)
                and (
                    p["attributes"].get("category")
                    or p["attributes"].get("category_name")
                )
            ),
            "attrs_brand": sum(
                1
                for p in sample
                if isinstance(p.get("attributes"), dict)
                and (p["attributes"].get("brand") or p["attributes"].get("marka"))
            ),
            "url_taxonomy_roots": dict(url_roots.most_common(8)),
            "url_taxonomy_coverage": round(top_n / max(len(sample), 1), 4),
        }

    rows = await conn.fetch(
        """
        SELECT m.id AS merchant_id, m.merchant_code, count(*)::bigint AS n,
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
          )) AS url,
          count(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM product_finance_options pfo
            JOIN product_offers o ON o.id=pfo.product_offer_id
            WHERE o.product_id=p.id AND pfo.eligibility_status='ELIGIBLE'
          )) AS finance
        FROM products p
        JOIN merchants m ON m.id=p.merchant_id
        WHERE p.status='ACTIVE'
        GROUP BY m.id, m.merchant_code
        ORDER BY n DESC
        """
    )

    merchants: list[dict[str, Any]] = []
    signals: list[MerchantPrioritySignals] = []
    for r in rows:
        n = int(r["n"])
        metrics = MerchantCoverageMetrics(
            active_products=n,
            searchable_products=n,
            category_coverage=_pct(int(r["cat"]), n),
            brand_coverage=_pct(int(r["brand"]), n),
            attribute_coverage=_pct(int(r["attrs"]), n),
            stock_coverage=1.0,
            card_media_coverage=_pct(int(r["media"]), n),
            fresh_price_coverage=_pct(int(r["fresh"]), n),
            valid_url_coverage=_pct(int(r["url"]), n),
            finance_coverage=_pct(int(r["finance"]), n),
            payment_plan_coverage=0.0,
        )
        decision = evaluate_merchant_readiness(metrics, thr)
        feed = feed_by_code.get(r["merchant_code"], {})
        cat_gap = max(0, int(round((thr.minimum_category_coverage - metrics.category_coverage) * n)))
        media_gap = max(0, int(round((thr.minimum_card_media_coverage - metrics.card_media_coverage) * n)))
        brand_gap = max(0, int(round((thr.minimum_brand_coverage - metrics.brand_coverage) * n)))
        attr_gap = max(
            0,
            int(
                round(
                    (thr.minimum_critical_attribute_coverage - metrics.attribute_coverage)
                    * n
                )
            ),
        )
        price_gap = max(
            0, int(round((thr.minimum_fresh_price_coverage - metrics.fresh_price_coverage) * n))
        )
        url_gap = max(
            0, int(round((thr.minimum_valid_url_coverage - metrics.valid_url_coverage) * n))
        )
        src = {
            "category": classify_source(feed, "category"),
            "brand": classify_source(feed, "brand"),
            "media": classify_source(feed, "media"),
        }
        sample_n = max(int(feed.get("sample") or 0), 1)
        # Cap recoverable by actual source hit counts (do not treat 9 brand rows as 666 recoverable).
        cat_src_hits = int(feed.get("cat_field") or 0) + int(feed.get("attrs_cat") or 0)
        tax_hits = int(round(float(feed.get("url_taxonomy_coverage") or 0.0) * sample_n))
        brand_src_hits = int(feed.get("brand") or 0) + int(feed.get("attrs_brand") or 0)
        media_src_hits = int(feed.get("img") or 0)
        # Scale sample hits to active product population when feed sample < n
        scale = n / sample_n if feed else 0.0

        def _avail(hits: int) -> int:
            return int(round(hits * scale)) if feed else 0

        recoverable = 0
        unrecoverable = 0
        for gap, avail in (
            (cat_gap, max(_avail(cat_src_hits), _avail(tax_hits))),
            (brand_gap, _avail(brand_src_hits)),
            (media_gap, _avail(media_src_hits)),
        ):
            if gap <= 0:
                continue
            take = min(gap, max(avail, 0))
            recoverable += take
            unrecoverable += max(0, gap - take)
        # activation_gap_score: lower is better; demand/finance reduce score
        volume_bonus = min(n / 1000.0, 5.0)
        finance_bonus = metrics.finance_coverage * 100
        activation_gap_score = (
            cat_gap
            + media_gap
            + brand_gap
            + attr_gap
            + price_gap
            + url_gap
            + unrecoverable * 2
            - volume_bonus * 10
            - finance_bonus
        )
        row = {
            "merchant_id": int(r["merchant_id"]),
            "merchant_code": r["merchant_code"],
            "active_products": n,
            "status": decision.status.value,
            "metrics": {
                "category_coverage": metrics.category_coverage,
                "brand_coverage": metrics.brand_coverage,
                "attribute_coverage": metrics.attribute_coverage,
                "card_media_coverage": metrics.card_media_coverage,
                "fresh_price_coverage": metrics.fresh_price_coverage,
                "valid_url_coverage": metrics.valid_url_coverage,
                "finance_coverage": metrics.finance_coverage,
            },
            "gaps": {
                "category_gap": cat_gap,
                "media_gap": media_gap,
                "brand_gap": brand_gap,
                "attribute_gap": attr_gap,
                "price_gap": price_gap,
                "url_gap": url_gap,
            },
            "source_availability": src,
            "feed": feed,
            "estimated_recoverable_count": recoverable,
            "unrecoverable_count": unrecoverable,
            "activation_gap_score": round(activation_gap_score, 3),
            "reasons": list(decision.reasons),
        }
        merchants.append(row)
        signals.append(
            MerchantPrioritySignals(
                merchant_id=int(r["merchant_id"]),
                active_products=n,
                category_coverage=metrics.category_coverage,
                media_coverage=metrics.card_media_coverage,
                price_freshness=metrics.fresh_price_coverage,
                finance_coverage=metrics.finance_coverage,
                payment_plan_coverage=0.0,
                unresolved_product_count=cat_gap,
                merchant_code=r["merchant_code"],
            )
        )

    # Third-merchant candidates: not READY, n>=min, prefer low gap + recoverable
    candidates = [
        m
        for m in merchants
        if m["status"] != "READY" and m["active_products"] >= thr.minimum_searchable_products
    ]
    candidates.sort(key=lambda m: (m["unrecoverable_count"], m["activation_gap_score"]))
    ranked = top_priority_merchants(signals, MerchantPriorityWeights(), limit=10)

    out = {
        "thresholds": thr.__dict__,
        "merchants": merchants,
        "ready": [m for m in merchants if m["status"] == "READY"],
        "third_merchant_candidates": candidates[:10],
        "selected_candidate": candidates[0] if candidates else None,
        "priority_top": [
            {"merchant_id": s.merchant_id, "merchant_code": s.merchant_code, "score": s.score}
            for s in ranked
        ],
    }
    print(json.dumps({
        "ready_count": len(out["ready"]),
        "ready": [(m["merchant_code"], m["active_products"]) for m in out["ready"]],
        "top_candidates": [
            {
                "code": m["merchant_code"],
                "n": m["active_products"],
                "status": m["status"],
                "gaps": m["gaps"],
                "src": m["source_availability"],
                "recoverable": m["estimated_recoverable_count"],
                "unrecoverable": m["unrecoverable_count"],
                "score": m["activation_gap_score"],
            }
            for m in candidates[:8]
        ],
    }, indent=2, ensure_ascii=False))
    await conn.close()
    return out


if __name__ == "__main__":
    asyncio.run(amain())

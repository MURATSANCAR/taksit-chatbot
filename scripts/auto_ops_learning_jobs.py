#!/usr/bin/env python3
"""Auto Ops learning / readiness side jobs (P2-LIVE).

Runs candidate generation, readiness snapshot, feed metrics, drift scan.
Does NOT promote mappings, create agreements, or swap ranking champions.

Intended to be invoked by auto_partner_ops on nanobase — not Mac local runtime.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _job(conn: Any, job_type: str, catalog_revision: str, fn) -> dict[str, Any]:
    job_id = uuid.uuid4()
    started = datetime.now(timezone.utc)
    try:
        await conn.execute(
            """
            INSERT INTO auto_ops_jobs
              (job_id, job_type, catalog_revision, attempt, status, started_at)
            VALUES ($1, $2, $3, 1, 'RUNNING', $4)
            """,
            job_id,
            job_type,
            catalog_revision,
            started,
        )
    except Exception:
        # Schema not applied yet
        result = await fn()
        return {"job_type": job_type, "status": "COMPLETED_NO_LEDGER", "result": result}

    try:
        result = await fn()
        await conn.execute(
            """
            UPDATE auto_ops_jobs
               SET status='COMPLETED', completed_at=NOW(), details=$2::jsonb
             WHERE job_id=$1
            """,
            job_id,
            json.dumps(result, default=str),
        )
        return {"job_type": job_type, "status": "COMPLETED", "result": result}
    except Exception as exc:  # noqa: BLE001
        await conn.execute(
            """
            UPDATE auto_ops_jobs
               SET status='FAILED', completed_at=NOW(), error_code=$2
             WHERE job_id=$1
            """,
            job_id,
            str(exc)[:500],
        )
        return {"job_type": job_type, "status": "FAILED", "error": str(exc)}


async def snapshot_feed_metrics(conn: Any, feed_dir: Path, catalog_revision: str) -> dict:
    active = await conn.fetchval("SELECT count(*) FROM products WHERE status='ACTIVE'")
    media = await conn.fetchval("SELECT count(*) FROM media_assets WHERE status='READY'")
    search = await conn.fetchval("SELECT count(*) FROM product_search_projection")
    finance = await conn.fetchval(
        "SELECT count(*) FROM product_finance_options WHERE eligibility_status='ELIGIBLE'"
    )
    feed_total = 0
    if feed_dir.exists():
        for path in feed_dir.glob("src-m-*.json"):
            try:
                feed_total += int(json.loads(path.read_text(encoding="utf-8")).get("count") or 0)
            except Exception:
                continue
    pending = max(0, feed_total - int(active or 0))
    try:
        await conn.execute(
            """
            INSERT INTO feed_processing_metrics (
              catalog_revision, feed_revision, feed_received_count,
              feed_processed_count, feed_pending_count, db_persisted_count,
              projection_ready_count, media_ready_count, finance_ready_count,
              search_ready_count
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            """,
            catalog_revision,
            f"feed_total={feed_total}",
            feed_total,
            int(active or 0),
            pending,
            int(active or 0),
            int(search or 0),
            int(media or 0),
            int(finance or 0),
            0,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "feed_total": feed_total, "active": active}
    return {
        "feed_received_count": feed_total,
        "db_persisted_count": int(active or 0),
        "feed_pending_count": pending,
        "media_ready_count": int(media or 0),
    }


async def recompute_merchant_readiness(conn: Any, catalog_revision: str) -> dict:
    from taksitlio.merchant_readiness import (
        MerchantCoverageMetrics,
        ReadinessThresholds,
        evaluate_merchant_readiness,
    )

    thr_row = None
    try:
        thr_row = await conn.fetchval(
            """
            SELECT thresholds FROM merchant_readiness_policy_versions
            WHERE status='ACTIVE' ORDER BY version DESC LIMIT 1
            """
        )
    except Exception:
        thr_row = None
    if isinstance(thr_row, str):
        try:
            thr_row = json.loads(thr_row)
        except Exception:
            thr_row = {}
    if not isinstance(thr_row, dict):
        thr_row = {}
    thr = ReadinessThresholds.from_mapping(thr_row)

    rows = await conn.fetch(
        """
        SELECT m.id, m.activation_gate, count(*)::bigint AS n,
          count(*) FILTER (WHERE p.category_id IS NOT NULL)::bigint AS with_cat,
          count(*) FILTER (WHERE p.brand_id IS NOT NULL)::bigint AS with_brand,
          count(*) FILTER (WHERE p.attributes IS NOT NULL
            AND p.attributes::text NOT IN ('{}','null'))::bigint AS with_attrs,
          count(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM product_offers o WHERE o.product_id=p.id
              AND o.stock_status IN ('AVAILABLE','LIMITED','OUT_OF_STOCK')
          ))::bigint AS with_stock,
          count(*) FILTER (WHERE EXISTS (
             SELECT 1 FROM product_media_links pml
               JOIN media_assets ma ON ma.id=pml.media_asset_id
              WHERE pml.product_id=p.id AND pml.is_primary AND ma.status='READY'
          ))::bigint AS with_media,
          count(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM product_offers o WHERE o.product_id=p.id
              AND o.freshness_status='FRESH'
          ))::bigint AS with_fresh,
          count(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM product_offers o WHERE o.product_id=p.id
              AND o.checkout_url IS NOT NULL AND length(o.checkout_url)>5
          ))::bigint AS with_url,
          count(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM product_finance_options pfo
              JOIN product_offers o ON o.id=pfo.product_offer_id
             WHERE o.product_id=p.id AND pfo.eligibility_status='ELIGIBLE'
          ))::bigint AS with_finance
        FROM products p JOIN merchants m ON m.id=p.merchant_id
        WHERE p.status='ACTIVE'
        GROUP BY m.id, m.activation_gate
        """
    )
    written = 0
    status_counts: dict[str, int] = {}
    for row in rows:
        n = int(row["n"])
        metrics = MerchantCoverageMetrics(
            active_products=n,
            searchable_products=n,
            category_coverage=int(row["with_cat"]) / max(n, 1),
            brand_coverage=int(row["with_brand"]) / max(n, 1),
            attribute_coverage=int(row["with_attrs"]) / max(n, 1),
            stock_coverage=int(row["with_stock"]) / max(n, 1),
            card_media_coverage=int(row["with_media"]) / max(n, 1),
            fresh_price_coverage=int(row["with_fresh"]) / max(n, 1),
            valid_url_coverage=int(row["with_url"]) / max(n, 1),
            finance_coverage=int(row["with_finance"]) / max(n, 1),
            payment_plan_coverage=0.0,
        )
        decision = evaluate_merchant_readiness(metrics, thr)
        status_counts[decision.status.value] = status_counts.get(decision.status.value, 0) + 1
        try:
            await conn.execute(
                """
                INSERT INTO merchant_readiness_snapshots (
                  merchant_id, catalog_revision, active_products, searchable_products,
                  category_coverage, brand_coverage, attribute_coverage, stock_coverage,
                  card_media_coverage, fresh_price_coverage, valid_url_coverage,
                  finance_coverage, payment_plan_coverage, critical_error_count,
                  status, reasons
                ) VALUES (
                  $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,0,$14,$15::jsonb
                )
                """,
                int(row["id"]),
                catalog_revision,
                n,
                n,
                metrics.category_coverage,
                metrics.brand_coverage,
                metrics.attribute_coverage,
                metrics.stock_coverage,
                metrics.card_media_coverage,
                metrics.fresh_price_coverage,
                metrics.valid_url_coverage,
                metrics.finance_coverage,
                metrics.payment_plan_coverage,
                decision.status.value,
                json.dumps(list(decision.reasons)),
            )
            written += 1
        except Exception:
            break
    return {"snapshots_written": written, "status_counts": status_counts}


async def amain() -> int:
    import asyncpg

    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--feed-dir",
        default=str(ROOT / "crawler" / "feeds" / "live"),
    )
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL required")

    conn = await asyncpg.connect(args.database_url)
    try:
        catalog_revision = str(
            await conn.fetchval("SELECT max(rebuilt_at) FROM product_search_projection")
            or _now()
        )
        results = []
        results.append(
            await _job(
                conn,
                "FEED_METRICS",
                catalog_revision,
                lambda: snapshot_feed_metrics(conn, Path(args.feed_dir), catalog_revision),
            )
        )
        results.append(
            await _job(
                conn,
                "MERCHANT_READINESS",
                catalog_revision,
                lambda: recompute_merchant_readiness(conn, catalog_revision),
            )
        )
        print(json.dumps({"captured_at": _now(), "jobs": results}, default=str))
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))

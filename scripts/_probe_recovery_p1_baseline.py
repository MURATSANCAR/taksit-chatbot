#!/usr/bin/env python3
"""Read-only baseline probe for recovery-p1 (production SELECT only)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any


def _ser(o: Any) -> Any:
    if o is None or isinstance(o, (str, int, float, bool)):
        return o
    if hasattr(o, "isoformat"):
        return o.isoformat()
    # Decimal / numeric
    try:
        from decimal import Decimal

        if isinstance(o, Decimal):
            return float(o)
    except Exception:
        pass
    if isinstance(o, dict):
        return {str(k): _ser(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_ser(x) for x in o]
    if isinstance(o, bytes):
        return o.decode("utf-8", errors="replace")
    return str(o)


async def main() -> None:
    import asyncpg

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL required")
    conn = await asyncpg.connect(url)
    try:
        await conn.execute("BEGIN TRANSACTION READ ONLY")
        out: dict[str, Any] = {"captured_at": datetime.now(timezone.utc).isoformat()}

        out["counts"] = {
            "products_active": await conn.fetchval(
                "SELECT count(*) FROM products WHERE status=$1", "ACTIVE"
            ),
            "offers": await conn.fetchval("SELECT count(*) FROM product_offers"),
            "merchants_active": await conn.fetchval(
                "SELECT count(*) FROM merchants WHERE status=$1", "ACTIVE"
            ),
            "brands_active": await conn.fetchval(
                "SELECT count(*) FROM brands WHERE status=$1", "ACTIVE"
            ),
            "categories_active": await conn.fetchval(
                "SELECT count(*) FROM categories WHERE status=$1", "ACTIVE"
            ),
            "with_brand": await conn.fetchval(
                "SELECT count(*) FROM products WHERE status=$1 AND brand_id IS NOT NULL",
                "ACTIVE",
            ),
            "with_category": await conn.fetchval(
                "SELECT count(*) FROM products WHERE status=$1 AND category_id IS NOT NULL",
                "ACTIVE",
            ),
            "finance_eligible": await conn.fetchval(
                "SELECT count(*) FROM product_finance_options WHERE eligibility_status=$1",
                "ELIGIBLE",
            ),
            "payment_plans": await conn.fetchval(
                "SELECT count(*) FROM payment_plan_calculations"
            ),
            "search_proj": await conn.fetchval(
                "SELECT count(*) FROM product_search_projection"
            ),
            "media_ready": await conn.fetchval(
                "SELECT count(*) FROM media_assets WHERE status=$1", "READY"
            ),
        }

        keys = await conn.fetch(
            """
            SELECT key, count(*)::bigint AS n FROM products p,
              LATERAL jsonb_object_keys(COALESCE(p.attributes,'{}'::jsonb)) AS key
            WHERE p.status=$1
            GROUP BY 1 ORDER BY n DESC LIMIT 40
            """,
            "ACTIVE",
        )
        out["top_attribute_keys"] = [dict(r) for r in keys]

        stock = await conn.fetch(
            "SELECT stock_status, count(*)::bigint AS n FROM product_offers GROUP BY 1 ORDER BY n DESC"
        )
        out["stock_status"] = [dict(r) for r in stock]

        camps = await conn.fetch(
            """
            SELECT id, campaign_code, display_name, status, verification_status,
                   source_reference, valid_from, valid_until,
                   minimum_purchase_amount, maximum_purchase_amount,
                   institution_id, financial_product_id, metadata
            FROM finance_campaigns
            """
        )
        out["campaigns"] = _ser([dict(r) for r in camps])

        ags = await conn.fetch(
            """
            SELECT a.id, a.status, a.valid_from, a.valid_until, a.source_reference,
                   a.merchant_id, a.institution_id, a.financial_product_id,
                   m.merchant_code, i.institution_code
            FROM merchant_financial_agreements a
            JOIN merchants m ON m.id=a.merchant_id
            JOIN financial_institutions i ON i.id=a.institution_id
            """
        )
        out["agreements"] = _ser([dict(r) for r in ags])

        rates = await conn.fetch(
            """
            SELECT id, campaign_id, financial_product_id, merchant_id, category_id,
                   rate_type, monthly_rate, verification_status, freshness_status,
                   source_reference, valid_from, valid_until,
                   minimum_amount, maximum_amount, minimum_term, maximum_term
            FROM finance_rate_snapshots
            """
        )
        out["rates"] = _ser([dict(r) for r in rates])

        cm = await conn.fetch(
            """
            SELECT cm.campaign_id, c.campaign_code, m.merchant_code
            FROM campaign_merchants cm
            JOIN finance_campaigns c ON c.id=cm.campaign_id
            JOIN merchants m ON m.id=cm.merchant_id
            ORDER BY 1,3
            """
        )
        out["campaign_merchants"] = [dict(r) for r in cm]

        ct = await conn.fetch(
            "SELECT campaign_id, term_months, included FROM campaign_terms ORDER BY 1,2"
        )
        out["campaign_terms"] = [dict(r) for r in ct]

        dq = await conn.fetch(
            "SELECT data_quality_status, count(*)::bigint AS n FROM product_data_quality_projection GROUP BY 1"
        )
        out["quality_projection"] = {str(r["data_quality_status"]): int(r["n"]) for r in dq}

        # source category candidates in attributes
        out["attr_category_presence"] = {
            "category": await conn.fetchval(
                "SELECT count(*) FROM products WHERE status=$1 AND attributes ? $2",
                "ACTIVE",
                "category",
            ),
            "source_category": await conn.fetchval(
                "SELECT count(*) FROM products WHERE status=$1 AND attributes ? $2",
                "ACTIVE",
                "source_category",
            ),
            "google_product_category": await conn.fetchval(
                "SELECT count(*) FROM products WHERE status=$1 AND attributes ? $2",
                "ACTIVE",
                "google_product_category",
            ),
        }

        tabs = await conn.fetch(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public' AND table_type='BASE TABLE'
            ORDER BY 1
            """
        )
        out["tables"] = [r["table_name"] for r in tabs]
        out["current_user"] = await conn.fetchval("SELECT current_user")
        out["can_create_db"] = await conn.fetchval(
            "SELECT has_database_privilege(current_user, 'CREATE')"
        )

        # merchant product distribution
        mdist = await conn.fetch(
            """
            SELECT m.merchant_code, m.activation_gate, count(*)::bigint AS n,
                   count(*) FILTER (WHERE p.category_id IS NOT NULL)::bigint AS with_cat,
                   count(*) FILTER (WHERE p.brand_id IS NOT NULL)::bigint AS with_brand
            FROM products p
            JOIN merchants m ON m.id=p.merchant_id
            WHERE p.status='ACTIVE'
            GROUP BY m.merchant_code, m.activation_gate
            ORDER BY n DESC
            """
        )
        out["merchant_product_dist"] = [dict(r) for r in mdist]

        # high-confidence title→category estimate using existing synonyms
        cats = await conn.fetch(
            "SELECT id, category_code, display_name, synonyms FROM categories WHERE status='ACTIVE'"
        )
        out["categories"] = _ser([dict(r) for r in cats])

        await conn.execute("ROLLBACK")
        path = sys.argv[1] if len(sys.argv) > 1 else "-"
        payload = json.dumps(_ser(out), ensure_ascii=False, indent=2) + "\n"
        if path == "-":
            sys.stdout.write(payload)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(payload)
            print(f"wrote {path}", file=sys.stderr)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

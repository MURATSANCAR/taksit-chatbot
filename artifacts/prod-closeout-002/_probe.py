#!/usr/bin/env python3
"""Read-only probe for PROD-CLOSEOUT-002."""
from __future__ import annotations

import asyncio
import json
import os
import sys


async def main() -> None:
    import asyncpg

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    out: dict = {}
    try:
        gcols = [
            c["column_name"]
            for c in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='continuous_golden_cases'"
            )
        ]
        out["golden_cols"] = gcols
        qcol = next(
            (c for c in ("raw_query", "query_text", "anonymized_query", "utterance") if c in gcols),
            None,
        )
        out["golden_query_col"] = qcol
        if qcol:
            rows = await conn.fetch(
                f"""
                SELECT id, lifecycle_status, provenance_class, LEFT(COALESCE({qcol}, ''), 160) AS q
                FROM continuous_golden_cases
                WHERE COALESCE({qcol}, '') <> ''
                ORDER BY id DESC LIMIT 20
                """
            )
            out["golden_sample"] = [dict(r) for r in rows]

        sqcols = [
            c["column_name"]
            for c in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='search_query_versions'"
            )
        ]
        out["sqv_cols"] = sqcols
        sq_q = next(
            (c for c in ("raw_user_text", "user_text", "anonymized_query", "query_text") if c in sqcols),
            None,
        )
        out["sqv_query_col"] = sq_q
        if sq_q:
            rows = await conn.fetch(
                f"""
                SELECT id, LEFT(COALESCE({sq_q}, ''), 160) AS q
                FROM search_query_versions
                WHERE COALESCE({sq_q}, '') <> ''
                ORDER BY id DESC LIMIT 30
                """
            )
            out["sqv_sample"] = [dict(r) for r in rows]

        if await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE tablename='public_real_shadow_unique_queries')"
        ):
            shcols = [
                c["column_name"]
                for c in await conn.fetch(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='public_real_shadow_unique_queries'"
                )
            ]
            out["shadow_cols"] = shcols
            rows = await conn.fetch("SELECT * FROM public_real_shadow_unique_queries LIMIT 10")
            out["shadow_sample"] = [dict(r) for r in rows]

        scols = [
            c["column_name"]
            for c in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='search_ready_product_projection'"
            )
        ]
        out["sr_cols"] = scols
        pcols = [
            c["column_name"]
            for c in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name='products'"
            )
        ]
        out["product_cols"] = pcols
        ocols = [
            c["column_name"]
            for c in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name='product_offers'"
            )
        ]
        out["offer_cols"] = ocols

        # finance merchants latest readiness
        latest = await conn.fetch(
            """
            SELECT DISTINCT ON (m.id)
              m.id AS merchant_id, m.display_name, r.status,
              r.category_coverage, r.brand_coverage, r.attribute_coverage,
              r.card_media_coverage, r.fresh_price_coverage, r.finance_coverage,
              r.active_products
            FROM merchants m
            JOIN merchant_financial_agreements a ON a.merchant_id=m.id AND a.status='ACTIVE'
            LEFT JOIN merchant_readiness_snapshots r ON r.merchant_id=m.id
            ORDER BY m.id, r.evaluated_at DESC NULLS LAST
            """
        )
        out["finance_merchants"] = [dict(r) for r in latest]

        stats = []
        for row in latest:
            mid = int(row["merchant_id"])
            with_brand = await conn.fetchval(
                """
                SELECT COUNT(*) FROM products p
                JOIN product_offers o ON o.product_id = p.id
                WHERE o.merchant_id=$1 AND p.brand_id IS NOT NULL AND p.category_id IS NOT NULL
                """,
                mid,
            )
            eligible = await conn.fetchval(
                """
                SELECT COUNT(*) FROM product_finance_options f
                JOIN product_offers o ON o.id = f.product_offer_id
                WHERE o.merchant_id=$1 AND f.eligibility_status='ELIGIBLE'
                """,
                mid,
            )
            # attributes presence
            attr_nonempty = 0
            if "attributes" in pcols:
                attr_nonempty = int(
                    await conn.fetchval(
                        """
                        SELECT COUNT(*) FROM products p
                        JOIN product_offers o ON o.product_id=p.id
                        WHERE o.merchant_id=$1 AND p.attributes IS NOT NULL
                          AND p.attributes::text NOT IN ('{}','[]','null')
                        """,
                        mid,
                    )
                    or 0
                )
            elif "specs" in pcols:
                attr_nonempty = int(
                    await conn.fetchval(
                        """
                        SELECT COUNT(*) FROM products p
                        JOIN product_offers o ON o.product_id=p.id
                        WHERE o.merchant_id=$1 AND p.specs IS NOT NULL
                        """,
                        mid,
                    )
                    or 0
                )
            stats.append(
                {
                    "merchant_id": mid,
                    "name": row["display_name"],
                    "with_brand_and_category": int(with_brand or 0),
                    "eligible_finance": int(eligible or 0),
                    "attr_nonempty": attr_nonempty,
                }
            )
        out["finance_merchant_stats"] = stats

        # RAM evidence in search-ready display names / titles if present
        name_expr = "COALESCE(p.display_name,'')"
        if "title" in pcols:
            name_expr = "COALESCE(p.title, p.display_name, '')"
        elif "name" in pcols:
            name_expr = "COALESCE(p.name, p.display_name, '')"
        ram_n = await conn.fetchval(
            f"""
            SELECT COUNT(*) FROM search_ready_product_projection s
            JOIN products p ON p.id = s.product_id
            WHERE {name_expr} ILIKE '%16%GB%' OR {name_expr} ILIKE '%16 GB%'
            """
        )
        out["search_ready_title_16gb_hint"] = int(ram_n or 0)
        out["name_expr_used"] = name_expr

        # cohort finance ready
        out["cohort"] = dict(
            await conn.fetchrow(
                """
                SELECT version, status, package_state, traffic_state,
                       search_ready_product_count, finance_ready_product_count, merchant_count
                FROM search_release_cohort_versions
                WHERE cohort_id=1 ORDER BY version DESC LIMIT 1
                """
            )
            or {}
        )
        json.dump(out, sys.stdout, indent=2, ensure_ascii=False, default=str)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

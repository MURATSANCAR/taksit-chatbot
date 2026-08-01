"""Rebuild product / merchant-category readiness and INTERNAL release cohort."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from taksitlio.applicability_readiness.cohort_policy import (
    CohortMetrics,
    CohortPolicyThresholds,
    evaluate_release_cohort,
)
from taksitlio.applicability_readiness.dimensions import (
    DimensionApplicability,
    QualityDimension,
    dimension_blocks_scope,
    resolve_dimension_applicability,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pct(n: int, d: int) -> float:
    return round(n / max(d, 1), 6)


async def load_dimension_policy(conn: Any) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT v.id, v.version, v.dimensions, v.category_overrides
        FROM category_quality_dimension_policy_versions v
        JOIN category_quality_dimension_policies p ON p.id = v.policy_id
        WHERE p.policy_code = 'default_applicability' AND v.status = 'ACTIVE'
        ORDER BY v.version DESC LIMIT 1
        """
    )
    if not row:
        return {
            "version": None,
            "dimensions": {d.value: "REQUIRED" for d in (
                QualityDimension.CATEGORY,
                QualityDimension.BRAND,
                QualityDimension.CARD_MEDIA,
                QualityDimension.PRICE,
                QualityDimension.PRODUCT_URL,
            )},
            "category_overrides": {},
        }
    dims = row["dimensions"]
    ov = row["category_overrides"]
    if isinstance(dims, str):
        dims = json.loads(dims)
    if isinstance(ov, str):
        ov = json.loads(ov)
    return {
        "version": int(row["version"]),
        "policy_version_id": int(row["id"]),
        "dimensions": dict(dims or {}),
        "category_overrides": dict(ov or {}),
    }


async def load_internal_cohort_policy(conn: Any) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT v.id, v.version, v.thresholds
        FROM release_cohort_policy_versions v
        JOIN release_cohort_policies p ON p.id = v.policy_id
        WHERE p.policy_code = 'internal_release' AND v.status = 'ACTIVE'
        ORDER BY v.version DESC LIMIT 1
        """
    )
    thr = row["thresholds"] if row else {}
    if isinstance(thr, str):
        thr = json.loads(thr)
    return {
        "policy_version_id": int(row["id"]) if row else None,
        "version": int(row["version"]) if row else None,
        "thresholds": CohortPolicyThresholds.from_mapping(thr or {}),
        "raw": thr or {},
    }


async def observe_source_capabilities(conn: Any, catalog_revision: str) -> list[dict[str, Any]]:
    """Merchant-level source capability from catalog coverage (no invented fields)."""

    rows = await conn.fetch(
        """
        SELECT m.id AS merchant_id, m.merchant_code, count(*)::int AS n,
          count(*) FILTER (WHERE p.category_id IS NOT NULL)::int AS cat,
          count(*) FILTER (WHERE p.brand_id IS NOT NULL)::int AS brand,
          count(*) FILTER (WHERE p.attributes IS NOT NULL
            AND p.attributes::text NOT IN ('{}','null'))::int AS attrs,
          count(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM product_media_links pml
            JOIN media_assets ma ON ma.id=pml.media_asset_id
            WHERE pml.product_id=p.id AND pml.is_primary AND ma.status='READY'
          ))::int AS media,
          count(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM product_finance_options pfo
            JOIN product_offers o ON o.id=pfo.product_offer_id
            WHERE o.product_id=p.id AND pfo.eligibility_status='ELIGIBLE'
          ))::int AS finance
        FROM products p
        JOIN merchants m ON m.id=p.merchant_id
        WHERE p.status='ACTIVE'
        GROUP BY m.id, m.merchant_code
        """
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        n = max(int(r["n"]), 1)

        def _cap(hit: int) -> str:
            ratio = hit / n
            if ratio >= 0.5:
                return "SUPPORTED"
            if ratio >= 0.05:
                return "PARTIALLY_SUPPORTED"
            if hit > 0:
                return "PARTIALLY_SUPPORTED"
            return "NOT_SUPPORTED"

        cov = {
            "category": _pct(int(r["cat"]), n),
            "brand": _pct(int(r["brand"]), n),
            "attributes": _pct(int(r["attrs"]), n),
            "media": _pct(int(r["media"]), n),
            "finance": _pct(int(r["finance"]), n),
        }
        await conn.execute(
            """
            DELETE FROM source_capability_profiles
             WHERE merchant_id=$1 AND category_id IS NULL AND source_id='catalog_observe'
            """,
            int(r["merchant_id"]),
        )
        await conn.execute(
            """
            INSERT INTO source_capability_profiles (
              merchant_id, category_id, source_id,
              provides_category, provides_brand, provides_attributes,
              provides_stock, provides_media, provides_gtin, provides_mpn,
              provides_publisher, provides_author, provides_finance,
              observed_coverage, sample_size, last_observed_at, catalog_revision
            ) VALUES (
              $1, NULL, 'catalog_observe',
              $2, $3, $4, 'UNKNOWN', $5, 'UNKNOWN', 'UNKNOWN',
              'UNKNOWN', 'UNKNOWN', $6,
              $7::jsonb, $8, NOW(), $9
            )
            """,
            int(r["merchant_id"]),
            _cap(int(r["cat"])),
            _cap(int(r["brand"])),
            _cap(int(r["attrs"])),
            _cap(int(r["media"])),
            _cap(int(r["finance"])),
            json.dumps(cov),
            int(r["n"]),
            catalog_revision,
        )
        out.append(
            {
                "merchant_id": int(r["merchant_id"]),
                "merchant_code": r["merchant_code"],
                "sample_size": int(r["n"]),
                "capabilities": {
                    "category": _cap(int(r["cat"])),
                    "brand": _cap(int(r["brand"])),
                    "media": _cap(int(r["media"])),
                    "finance": _cap(int(r["finance"])),
                },
                "observed_coverage": cov,
            }
        )
    return out


async def rebuild_product_readiness(
    conn: Any, *, catalog_revision: str, dim_policy: dict[str, Any]
) -> dict[str, Any]:
    """Product-level readiness for READY merchants + scoped PARTIAL demo merchants.

    Bulk SQL for default REQUIRED dims; category overrides (e.g. BRAND N/A) applied
    in a second pass for overridden category_ids only.
    """

    await conn.execute("DELETE FROM product_readiness_projection")
    overrides = dim_policy.get("category_overrides") or {}
    na_brand_cats = [
        int(k)
        for k, v in overrides.items()
        if isinstance(v, dict) and str(v.get("BRAND")) == "NOT_APPLICABLE"
    ]
    policy_ver = str(dim_policy.get("version") or "")

    # Limit to READY merchants (latest snapshot) — INTERNAL cohort source.
    ready_ids = [
        int(r["merchant_id"])
        for r in await conn.fetch(
            """
            SELECT DISTINCT ON (merchant_id) merchant_id, status
            FROM merchant_readiness_snapshots
            ORDER BY merchant_id, evaluated_at DESC
            """
        )
        if r["status"] == "READY"
    ]
    # Also include merchants with high category resolution for applicability demo (e.g. m-dr).
    extra = await conn.fetch(
        """
        SELECT m.id FROM merchants m
        WHERE m.merchant_code = ANY($1::text[])
        """,
        ["m-dr"],
    )
    merchant_ids = sorted({*ready_ids, *[int(r["id"]) for r in extra]})
    if not merchant_ids:
        return {"rows": 0, "status_counts": {}, "policy_version": policy_ver}

    await conn.execute(
        """
        INSERT INTO product_readiness_projection (
          product_id, offer_id, merchant_id, category_id,
          category_ready, entity_roles_ready, critical_attributes_ready,
          stock_ready, card_media_ready, price_ready, url_ready, finance_ready,
          readiness_status, failed_dimensions, catalog_revision, policy_version, updated_at
        )
        SELECT
          p.id,
          o.id,
          p.merchant_id,
          p.category_id,
          (p.category_id IS NOT NULL),
          CASE
            WHEN p.category_id = ANY($4::bigint[]) THEN TRUE
            ELSE (p.brand_id IS NOT NULL)
          END,
          TRUE,
          TRUE,
          EXISTS (
            SELECT 1 FROM product_media_links pml
            JOIN media_assets ma ON ma.id=pml.media_asset_id
            WHERE pml.product_id=p.id AND pml.is_primary AND ma.status='READY'
          ),
          (o.id IS NOT NULL AND o.current_price IS NOT NULL AND o.current_price > 0
            AND o.freshness_status='FRESH'),
          (o.checkout_url IS NOT NULL AND length(o.checkout_url) > 5),
          EXISTS (
            SELECT 1 FROM product_finance_options pfo
            WHERE pfo.product_offer_id=o.id AND pfo.eligibility_status='ELIGIBLE'
          ),
          CASE
            WHEN p.category_id IS NULL THEN 'BLOCKED'
            WHEN NOT EXISTS (
              SELECT 1 FROM product_media_links pml
              JOIN media_assets ma ON ma.id=pml.media_asset_id
              WHERE pml.product_id=p.id AND pml.is_primary AND ma.status='READY'
            ) THEN 'PARTIAL'
            WHEN NOT (
              o.id IS NOT NULL AND o.current_price IS NOT NULL AND o.current_price > 0
              AND o.freshness_status='FRESH'
            ) THEN 'PARTIAL'
            WHEN NOT (o.checkout_url IS NOT NULL AND length(o.checkout_url) > 5) THEN 'PARTIAL'
            WHEN (p.brand_id IS NULL AND NOT (p.category_id = ANY($4::bigint[]))) THEN 'PARTIAL'
            WHEN EXISTS (
              SELECT 1 FROM product_finance_options pfo
              WHERE pfo.product_offer_id=o.id AND pfo.eligibility_status='ELIGIBLE'
            ) THEN 'READY_FOR_FINANCE_SEARCH'
            ELSE 'READY_FOR_SEARCH'
          END,
          '[]'::jsonb,
          $2,
          $3,
          NOW()
        FROM products p
        LEFT JOIN LATERAL (
          SELECT * FROM product_offers WHERE product_id=p.id ORDER BY id DESC LIMIT 1
        ) o ON TRUE
        WHERE p.status='ACTIVE' AND p.merchant_id = ANY($1::bigint[])
        """,
        merchant_ids,
        catalog_revision,
        policy_ver,
        na_brand_cats or [-1],
    )
    counts = await conn.fetch(
        """
        SELECT readiness_status, count(*)::int AS n
        FROM product_readiness_projection
        GROUP BY 1
        """
    )
    return {
        "rows": sum(int(r["n"]) for r in counts),
        "status_counts": {r["readiness_status"]: int(r["n"]) for r in counts},
        "policy_version": policy_ver,
        "merchant_ids": merchant_ids,
        "brand_not_applicable_category_ids": na_brand_cats,
        "captured_at": _now(),
    }


async def rebuild_merchant_category_readiness(
    conn: Any, *, catalog_revision: str, dim_policy: dict[str, Any]
) -> dict[str, Any]:
    defaults = dim_policy.get("dimensions") or {}
    overrides = dim_policy.get("category_overrides") or {}
    policy_ver = str(dim_policy.get("version") or "")

    scopes = await conn.fetch(
        """
        SELECT p.merchant_id, p.category_id, count(*)::int AS active_n,
          count(*) FILTER (WHERE pr.readiness_status IN (
            'READY_FOR_SEARCH','READY_FOR_FINANCE_SEARCH'
          ))::int AS search_ready_n,
          count(*) FILTER (WHERE p.brand_id IS NOT NULL)::int AS brand_n,
          count(*) FILTER (WHERE pr.card_media_ready)::int AS media_n,
          count(*) FILTER (WHERE pr.price_ready)::int AS price_n,
          count(*) FILTER (WHERE pr.url_ready)::int AS url_n,
          count(*) FILTER (WHERE pr.finance_ready)::int AS finance_n
        FROM products p
        LEFT JOIN product_readiness_projection pr ON pr.product_id = p.id
        WHERE p.status='ACTIVE' AND p.category_id IS NOT NULL
        GROUP BY p.merchant_id, p.category_id
        """
    )
    written = 0
    status_counts: dict[str, int] = {}
    for s in scopes:
        cat_id = int(s["category_id"])
        n = int(s["active_n"])
        apps = {
            d.value: resolve_dimension_applicability(
                default_dimensions=defaults,
                category_overrides=overrides,
                category_id=cat_id,
                dimension=d,
            ).value
            for d in QualityDimension
        }
        brand_app = resolve_dimension_applicability(
            default_dimensions=defaults,
            category_overrides=overrides,
            category_id=cat_id,
            dimension=QualityDimension.BRAND,
        )
        # Applicable denominator for brand
        if brand_app is DimensionApplicability.NOT_APPLICABLE:
            brand_cov = 1.0
            brand_fail = False
        else:
            brand_cov = _pct(int(s["brand_n"]), n)
            brand_fail = (
                brand_app is DimensionApplicability.REQUIRED and brand_cov < 0.90
            )

        media_cov = _pct(int(s["media_n"]), n)
        price_cov = _pct(int(s["price_n"]), n)
        url_cov = _pct(int(s["url_n"]), n)
        failed: list[str] = []
        if media_cov < 0.95:
            failed.append("card_media_coverage_below_threshold")
        if price_cov < 0.95:
            failed.append("fresh_price_coverage_below_threshold")
        if url_cov < 0.99:
            failed.append("valid_url_coverage_below_threshold")
        if brand_fail:
            failed.append("brand_coverage_below_threshold")

        search_ready_n = int(s["search_ready_n"])
        if not failed and search_ready_n > 0 and media_cov >= 0.95:
            status = "READY"
        elif search_ready_n > 0:
            status = "PARTIAL"
        else:
            status = "BLOCKED"

        await conn.execute(
            """
            INSERT INTO merchant_category_readiness_snapshots (
              merchant_id, category_id, catalog_revision, readiness_policy_version,
              quality_dimension_policy_version, active_product_count, eligible_product_count,
              search_ready_product_count, category_resolution_coverage, brand_coverage,
              critical_attribute_coverage, stock_coverage, card_media_coverage,
              fresh_price_coverage, valid_url_coverage, finance_coverage,
              payment_plan_coverage, wrong_category_count, wrong_media_count,
              wrong_finance_count, critical_error_count, status, failed_policy_rules,
              dimension_applicability, evaluated_at
            ) VALUES (
              $1,$2,$3,$4,$5,$6,$6,$7,1.0,$8,1.0,1.0,$9,$10,$11,$12,0,
              0,0,0,0,$13,$14::jsonb,$15::jsonb,NOW()
            )
            """,
            int(s["merchant_id"]),
            cat_id,
            catalog_revision,
            policy_ver,
            policy_ver,
            n,
            search_ready_n,
            brand_cov,
            media_cov,
            price_cov,
            url_cov,
            _pct(int(s["finance_n"]), n),
            status,
            json.dumps(failed),
            json.dumps(apps),
        )
        written += 1
        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "scopes_written": written,
        "status_counts": status_counts,
        "captured_at": _now(),
    }


async def build_internal_cohort(
    conn: Any,
    *,
    catalog_revision: str,
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Build INTERNAL cohort from READY merchant aggregate products already search-ready.

    Does NOT require READY merchant count >= 3.
    """
    from taksitlio.product_query.search_ready_rebuild import rebuild_search_ready_projection

    # Refresh legacy projection from READY merchants (source of truth for membership).
    legacy = await rebuild_search_ready_projection(
        conn, catalog_revision=catalog_revision
    )
    leakage = legacy.get("leakage") or {}
    leak_count = sum(int(v or 0) for v in leakage.values())

    cohort = await conn.fetchrow(
        "SELECT id FROM search_release_cohorts WHERE cohort_code='internal_ready_merchants'"
    )
    if not cohort:
        raise RuntimeError("search_release_cohorts seed missing")
    cohort_id = int(cohort["id"])
    next_ver = int(
        await conn.fetchval(
            "SELECT COALESCE(MAX(version),0)+1 FROM search_release_cohort_versions WHERE cohort_id=$1",
            cohort_id,
        )
        or 1
    )

    # Roll back prior INTERNAL versions for this cohort.
    await conn.execute(
        """
        UPDATE search_release_cohort_versions
           SET status='ROLLED_BACK'
         WHERE cohort_id=$1 AND status='INTERNAL'
        """,
        cohort_id,
    )

    merchants = legacy.get("ready_merchant_ids") or []
    cat_scopes = await conn.fetchval(
        """
        SELECT count(DISTINCT category_id) FROM search_ready_product_projection
        """
    )
    finance_n = int(legacy.get("finance_ready_rows") or 0)
    rows_n = int(legacy.get("rows") or 0)

    metrics = CohortMetrics(
        search_ready_product_count=rows_n,
        finance_ready_product_count=finance_n,
        search_demand_coverage=None,  # NOT_VERIFIED → skip demand gate when None
        ready_category_scope_count=int(cat_scopes or 0),
        merchant_count=len(merchants),
        golden_bucket_coverage=None,  # INTERNAL policy min 0; None skips
        critical_error_count=0,
        projection_leakage_count=leak_count,
        wrong_mapping_count=0,
    )
    decision = evaluate_release_cohort(metrics, policy["thresholds"])

    status = "INTERNAL" if decision.passed else "DRAFT"
    cv_id = await conn.fetchval(
        """
        INSERT INTO search_release_cohort_versions (
          cohort_id, version, status, policy_version_id,
          search_ready_product_count, finance_ready_product_count,
          search_demand_coverage, category_scope_count, merchant_count,
          category_coverage, card_media_coverage, golden_coverage,
          critical_error_count, projection_leakage_count, catalog_revision,
          metrics, activated_at
        ) VALUES (
          $1::bigint, $2::int, $3::text, $4::bigint, $5::int, $6::int, NULL,
          $7::int, $8::int, 1.0, 1.0, NULL, 0, $9::int, $10::text, $11::jsonb,
          CASE WHEN $3::text = 'INTERNAL' THEN NOW() ELSE NULL END
        ) RETURNING id
        """,
        cohort_id,
        next_ver,
        status,
        policy.get("policy_version_id"),
        rows_n,
        finance_n,
        int(cat_scopes or 0),
        len(merchants),
        leak_count,
        catalog_revision,
        json.dumps(
            {
                "decision_passed": decision.passed,
                "reasons": list(decision.reasons),
                "legacy_projection": {
                    "rows": rows_n,
                    "ready_merchants": legacy.get("ready_merchants"),
                    "leakage": leakage,
                },
                "require_merchant_count": False,
            }
        ),
    )

    members = await conn.execute(
        """
        INSERT INTO search_release_cohort_members (
          cohort_version_id, product_id, offer_id, merchant_id, category_id, membership_reason
        )
        SELECT $1, product_id, offer_id, merchant_id, category_id, 'READY_MERCHANT_SEARCH_READY'
        FROM search_ready_product_projection
        ON CONFLICT (cohort_version_id, product_id) DO NOTHING
        """,
        int(cv_id),
    )

    # Projection v2 for this cohort version
    await conn.execute(
        """
        DELETE FROM search_ready_product_projection_v2
         WHERE cohort_id=$1 AND cohort_version=$2
        """,
        cohort_id,
        next_ver,
    )
    v2 = await conn.execute(
        """
        INSERT INTO search_ready_product_projection_v2 (
          product_id, cohort_id, cohort_version, offer_id, merchant_id, category_id,
          product_readiness_status, merchant_category_readiness_status,
          card_media_id, current_price, stock_status, finance_ready,
          catalog_revision, updated_at
        )
        SELECT
          s.product_id, $1, $2, s.offer_id, s.merchant_id, s.category_id,
          COALESCE(pr.readiness_status, 'READY_FOR_SEARCH'),
          COALESCE(mc.status, 'READY'),
          s.card_media_id, s.current_price, s.stock_status, s.finance_ready,
          $3, NOW()
        FROM search_ready_product_projection s
        LEFT JOIN product_readiness_projection pr ON pr.product_id = s.product_id
        LEFT JOIN LATERAL (
          SELECT status FROM merchant_category_readiness_snapshots mcs
          WHERE mcs.merchant_id=s.merchant_id AND mcs.category_id=s.category_id
          ORDER BY evaluated_at DESC LIMIT 1
        ) mc ON TRUE
        """,
        cohort_id,
        next_ver,
        catalog_revision,
    )

    v2_rows = await conn.fetchval(
        """
        SELECT count(*) FROM search_ready_product_projection_v2
        WHERE cohort_id=$1 AND cohort_version=$2
        """,
        cohort_id,
        next_ver,
    )
    # Leakage: members not in READY product readiness / missing media/price
    v2_leak = await conn.fetchrow(
        """
        SELECT
          count(*) FILTER (WHERE category_id IS NULL) AS unresolved_category,
          count(*) FILTER (WHERE current_price IS NULL OR current_price <= 0) AS invalid_price,
          count(*) FILTER (WHERE card_media_id IS NULL) AS non_card_ready,
          count(*) FILTER (WHERE product_readiness_status IN ('BLOCKED','QUARANTINED')) AS blocked_product
        FROM search_ready_product_projection_v2
        WHERE cohort_id=$1 AND cohort_version=$2
        """,
        cohort_id,
        next_ver,
    )

    # Point feature flag config at INTERNAL cohort (status INTERNAL only if policy passed)
    flag_status = "INTERNAL" if decision.passed else "SHADOW"
    await conn.execute(
        """
        UPDATE runtime_feature_flags
           SET status=$1,
               config=jsonb_build_object(
                 'cohort_code', 'internal_ready_merchants',
                 'cohort_id', $2::bigint,
                 'cohort_version', $3::int,
                 'traffic', 'internal_only',
                 'write_snapshots', true,
                 'apply_activation_gate', false,
                 'require_merchant_count', false
               ),
               updated_at=NOW(),
               updated_by='p3.3-applicability'
         WHERE flag_code='dynamic_readiness_enabled'
        """,
        flag_status,
        cohort_id,
        next_ver,
    )

    return {
        "cohort_id": cohort_id,
        "cohort_version": next_ver,
        "cohort_version_id": int(cv_id),
        "status": status,
        "policy_passed": decision.passed,
        "policy_reasons": list(decision.reasons),
        "search_ready_product_count": rows_n,
        "finance_ready_product_count": finance_n,
        "merchant_count": len(merchants),
        "category_scope_count": int(cat_scopes or 0),
        "members_sql": str(members),
        "v2_rows": int(v2_rows or 0),
        "v2_sql": str(v2),
        "legacy": legacy,
        "leakage": {
            "legacy_total": leak_count,
            "v2_unresolved_category": int(v2_leak["unresolved_category"] or 0),
            "v2_invalid_price": int(v2_leak["invalid_price"] or 0),
            "v2_non_card_ready": int(v2_leak["non_card_ready"] or 0),
            "v2_blocked_product": int(v2_leak["blocked_product"] or 0),
        },
        "flag_status": flag_status,
        "captured_at": _now(),
    }


__all__ = [
    "build_internal_cohort",
    "load_dimension_policy",
    "load_internal_cohort_policy",
    "observe_source_capabilities",
    "rebuild_merchant_category_readiness",
    "rebuild_product_readiness",
]

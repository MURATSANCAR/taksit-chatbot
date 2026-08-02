"""Internal evidence dashboard — reads source-of-truth tables only.

No hardcoded progress counts. Does not invent humans or shadow uniques.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from taksitlio.api.deps import container_from

router = APIRouter(tags=["admin-evidence"])


def _require_token(
    x_taksitlio_internal_token: Optional[str] = None,
    x_admin_token: Optional[str] = None,
) -> None:
    expected = (os.environ.get("TAKSITLIO_INTERNAL_TOKEN") or "").strip()
    presented = (x_taksitlio_internal_token or x_admin_token or "").strip()
    if not expected or presented != expected:
        raise HTTPException(status_code=403, detail="unauthorized")


def _pool(request: Request):
    container = container_from(request)
    pool = (
        container.extras.get("db_pool")
        or container.extras.get("pg_pool")
        or container.extras.get("pool")
    )
    if pool is None:
        raise HTTPException(status_code=501, detail="db_pool not configured")
    return pool


async def _dashboard_metrics(conn: Any) -> dict[str, Any]:
    unique_shadow = int(
        await conn.fetchval("SELECT count(*) FROM public_real_shadow_unique_queries")
        or 0
    )
    # Fallback to observations unique if unique table empty (pre-materialize)
    if unique_shadow == 0:
        unique_shadow = int(
            await conn.fetchval(
                """
                SELECT count(DISTINCT lower(trim(anonymized_query)))
                FROM public_shadow_observations
                """
            )
            or 0
        )
    completed_shadow = int(
        await conn.fetchval("SELECT count(*) FROM public_shadow_observations") or 0
    )
    real_session_unique = int(
        await conn.fetchval(
            """
            SELECT count(DISTINCT lower(trim(raw_user_text)))
            FROM search_query_versions
            WHERE raw_user_text IS NOT NULL AND length(trim(raw_user_text)) > 2
            """
        )
        or 0
    )
    ratio = (unique_shadow / completed_shadow) if completed_shadow else 0.0

    thr_row = await conn.fetchrow(
        """
        SELECT v.thresholds FROM public_shadow_diversity_policy_versions v
        JOIN public_shadow_diversity_policies p ON p.id=v.policy_id
        WHERE p.policy_code='product_search_shadow_diversity' AND v.status='ACTIVE'
        ORDER BY v.version DESC LIMIT 1
        """
    )
    thr = thr_row["thresholds"] if thr_row else {}
    if isinstance(thr, str):
        thr = json.loads(thr)

    review_pending = int(
        await conn.fetchval(
            """
            SELECT count(*) FROM public_shadow_difference_reviews
            WHERE human_class IS NULL
            """
        )
        or 0
    )
    review_done = int(
        await conn.fetchval(
            """
            SELECT count(*) FROM public_shadow_difference_reviews
            WHERE human_class IS NOT NULL
            """
        )
        or 0
    )
    mis_crit = int(
        await conn.fetchval(
            """
            SELECT count(*) FROM public_shadow_difference_reviews
            WHERE human_class='MISCLASSIFIED_CRITICAL'
            """
        )
        or 0
    )

    golden = await conn.fetch(
        """
        SELECT provenance_class, lifecycle_status, count(*)::int n
        FROM continuous_golden_cases
        GROUP BY 1, 2
        """
    )
    human_verified = int(
        await conn.fetchval(
            """
            SELECT count(*) FROM continuous_golden_cases
            WHERE lifecycle_status='APPROVED' AND provenance_class='HUMAN_VERIFIED'
            """
        )
        or 0
    )
    oos_hv = int(
        await conn.fetchval(
            """
            SELECT count(*) FROM continuous_golden_cases
            WHERE lifecycle_status='APPROVED' AND provenance_class='HUMAN_VERIFIED'
              AND lower(coalesce(bucket, expected->>'bucket')) LIKE '%out_of_scope%'
            """
        )
        or 0
    )
    bucket_gaps = await conn.fetch(
        """
        SELECT lower(coalesce(bucket, expected->>'bucket', 'unknown')) b, count(*)::int n
        FROM continuous_golden_cases
        WHERE lifecycle_status='APPROVED' AND provenance_class='HUMAN_VERIFIED'
        GROUP BY 1
        """
    )

    participants = await conn.fetch(
        """
        SELECT role_family, count(*)::int n FROM public_uat_participants
        GROUP BY 1
        """
    )
    uat_human = int(
        await conn.fetchval(
            """
            SELECT count(*) FROM public_uat_cases
            WHERE evidence_class='HUMAN_PANEL' AND human_participant_id IS NOT NULL
            """
        )
        or 0
    )
    uat_by_role = await conn.fetch(
        """
        SELECT reviewer_role, count(*)::int n FROM public_uat_cases
        WHERE evidence_class='HUMAN_PANEL'
        GROUP BY 1
        """
    )

    traffic = await conn.fetchrow(
        """
        SELECT version, status, package_state, traffic_state, catalog_revision
        FROM search_release_cohort_versions
        WHERE status='PUBLIC_CANARY'
        ORDER BY version DESC LIMIT 1
        """
    )

    return {
        "shadow": {
            "completed_observations": completed_shadow,
            "unique_normalized": unique_shadow,
            "unique_ratio": round(ratio, 4),
            "real_session_unique_queries": real_session_unique,
            "policy_minimum_unique": int((thr or {}).get("minimum_unique_normalized_queries") or 500),
        },
        "shadow_review": {
            "pending": review_pending,
            "completed": review_done,
            "misclassified_critical": mis_crit,
        },
        "golden": {
            "human_verified_approved": human_verified,
            "out_of_scope_human_verified": oos_hv,
            "by_provenance_lifecycle": [
                {
                    "provenance_class": r["provenance_class"],
                    "lifecycle_status": r["lifecycle_status"],
                    "n": r["n"],
                }
                for r in golden
            ],
            "human_verified_buckets": {r["b"]: r["n"] for r in bucket_gaps},
        },
        "uat": {
            "participants_by_role": {r["role_family"]: r["n"] for r in participants},
            "human_panel_cases": uat_human,
            "human_cases_by_role": {r["reviewer_role"]: r["n"] for r in uat_by_role},
        },
        "canary": dict(traffic) if traffic else None,
        "source": "DATABASE_QUERY",
        "hardcoded": False,
    }


@router.get("/evidence/dashboard")
async def evidence_dashboard_json(
    request: Request,
    x_taksitlio_internal_token: Optional[str] = Header(default=None),
    x_admin_token: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_token(x_taksitlio_internal_token, x_admin_token)
    pool = _pool(request)
    async with pool.acquire() as conn:
        return await _dashboard_metrics(conn)


@router.get("/evidence/dashboard/ui", response_class=HTMLResponse)
async def evidence_dashboard_ui(
    request: Request,
    x_taksitlio_internal_token: Optional[str] = Header(default=None),
    x_admin_token: Optional[str] = Header(default=None),
) -> HTMLResponse:
    _require_token(x_taksitlio_internal_token, x_admin_token)
    pool = _pool(request)
    async with pool.acquire() as conn:
        m = await _dashboard_metrics(conn)
    body = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>P4.2 Evidence Dashboard</title>
<style>
body{{font-family:ui-sans-serif,system-ui;margin:2rem;background:#f7f7f5;color:#1a1a1a}}
h1{{font-size:1.4rem}} section{{background:#fff;border:1px solid #ddd;padding:1rem 1.25rem;margin:1rem 0}}
pre{{white-space:pre-wrap;font-size:12px}} .warn{{color:#8a1f1f}}
</style></head><body>
<h1>Canary evidence dashboard (source-of-truth)</h1>
<p>Counts are live DB queries. Synthetic progress is not shown.</p>
<section><h2>Shadow</h2><pre>{json.dumps(m["shadow"], indent=2, ensure_ascii=False)}</pre></section>
<section><h2>Shadow review</h2><pre>{json.dumps(m["shadow_review"], indent=2)}</pre></section>
<section><h2>Golden</h2><pre>{json.dumps(m["golden"], indent=2, ensure_ascii=False)}</pre></section>
<section><h2>UAT</h2><pre>{json.dumps(m["uat"], indent=2)}</pre></section>
<section><h2>Canary</h2><pre>{json.dumps(m["canary"], indent=2, default=str)}</pre></section>
<p class="warn">Live %5 traffic is never started from this page.</p>
</body></html>"""
    return HTMLResponse(body)

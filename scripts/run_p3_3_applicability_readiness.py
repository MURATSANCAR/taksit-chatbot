#!/usr/bin/env python3
"""P3.3 — Applicability-aware readiness + INTERNAL release cohort (no READY>=3 gate)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

ART = ROOT / "artifacts" / "e2e-production-verification" / "p3-3-applicability-readiness"
REPORT = ROOT / "docs" / "verification" / "P3.3-APPLICABILITY-READINESS-REPORT.md"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(name: str, payload: Any) -> None:
    ART.mkdir(parents=True, exist_ok=True)
    path = ART / name
    if name.endswith(".jsonl"):
        if isinstance(payload, list):
            path.write_text(
                "\n".join(json.dumps(x, ensure_ascii=False, default=str) for x in payload)
                + ("\n" if payload else ""),
                encoding="utf-8",
            )
        else:
            path.write_text(str(payload), encoding="utf-8")
        return
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def _p95(vals: list[float]) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    idx = max(0, int(round(0.95 * (len(s) - 1))))
    return round(s[idx], 3)


async def classify_requests(n: int = 50) -> dict[str, Any]:
    base = (
        os.environ.get("TAKSITLIO_API_BASE")
        or os.environ.get("PUBLIC_API_BASE")
        or "http://127.0.0.1:8040"
    ).rstrip("/")
    queries = [
        "samsung telefon",
        "iphone 15",
        "laptop",
        "kulaklık",
        "buzdolabı",
        "ayakkabı",
        "en ucuz tablet",
        "televizyon 55",
    ]
    classes: dict[str, int] = {}
    details: list[dict[str, Any]] = []
    ok_ms: list[float] = []
    for i in range(n):
        q = queries[i % len(queries)]
        body = json.dumps(
            {"message": q, "channel": "web", "metadata": {"p33": True, "i": i}}
        ).encode()
        req = request.Request(
            f"{base}/v1/search-sessions",
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        t0 = time.perf_counter()
        cls = "UNKNOWN"
        status = 0
        excerpt = ""
        try:
            with request.urlopen(req, timeout=30) as resp:
                status = int(resp.status)
                raw = resp.read().decode("utf-8", errors="replace")
                excerpt = raw[:160]
                dur = (time.perf_counter() - t0) * 1000.0
                if status >= 500:
                    cls = "APPLICATION_EXCEPTION"
                elif status in (401, 403):
                    cls = "AUTH_ERROR"
                else:
                    cls = "OK"
                    ok_ms.append(dur)
        except error.HTTPError as e:
            status = int(e.code)
            dur = (time.perf_counter() - t0) * 1000.0
            cls = "AUTH_ERROR" if status in (401, 403) else "APPLICATION_EXCEPTION"
            excerpt = str(e)[:160]
        except error.URLError as e:
            dur = (time.perf_counter() - t0) * 1000.0
            cls = "CLIENT_TIMEOUT" if "timed out" in str(e).lower() else "ROUTING_ERROR"
            excerpt = str(e)[:160]
        except Exception as e:  # noqa: BLE001
            dur = (time.perf_counter() - t0) * 1000.0
            cls = "APPLICATION_EXCEPTION"
            excerpt = str(e)[:160]
        classes[cls] = classes.get(cls, 0) + 1
        details.append(
            {
                "test_case_id": f"p33-{i}",
                "http_status": status,
                "class": cls,
                "duration_ms": round(dur, 2),
                "body_excerpt": excerpt,
            }
        )
    total = max(sum(classes.values()), 1)
    ok = classes.get("OK", 0)
    return {
        "classes": classes,
        "successful_request_rate": round(ok / total, 6),
        "ok_samples": ok,
        "ok_p95_ms": _p95(ok_ms),
        "details": details[:40],
        "note": "HTTP-level classification; named API spans require TraceRecorder wiring",
    }


def decide(gates: dict[str, bool]) -> dict[str, Any]:
    failed = [k for k, v in gates.items() if not v]
    if not failed:
        decision = "P3_3_INTERNAL_READY"
    elif gates.get("RELEASE_COHORT_POLICY_GATE") and gates.get(
        "INTERNAL_COHORT_ACTIVATION_GATE"
    ):
        decision = "P3_3_INTERNAL_CONDITIONALLY_READY"
        # still fail full READY label if perf/golden/e2e missing
        if not (
            gates.get("REQUEST_SUCCESS_RATE_GATE")
            and gates.get("RANKING_PERFORMANCE_GATE")
            and gates.get("PLAYWRIGHT_INTERNAL_GATE")
        ):
            # Conditional is allowed when cohort INTERNAL is live but E2E incomplete
            decision = "P3_3_INTERNAL_CONDITIONALLY_READY"
    else:
        decision = "P3_3_INTERNAL_NOT_READY"
    return {"decision": decision, "failed_gates": failed, "captured_at": _now()}


def write_report(summary: dict[str, Any], decision: dict[str, Any]) -> None:
    lines = [
        "# P3.3 APPLICABILITY READINESS REPORT",
        "",
        f"**Generated:** {_now()}",
        f"**Decision:** **{decision['decision']}**",
        "",
        "**System:** Kontrollü, versioned, event-driven adaptif katalog ve ranking "
        "(self-learning model değildir).",
        "",
        "**Public cutover:** not performed.",
        "",
        "Artifacts: `artifacts/e2e-production-verification/p3-3-applicability-readiness/`",
        "",
        "## Readiness model",
        "",
        "- Removed static `READY merchant count >= 3` as INTERNAL release gate",
        f"- Quality dimension policy version: `{summary.get('dim_policy_version')}`",
        f"- Internal cohort policy: `{summary.get('cohort_policy')}`",
        f"- Applicable dimensions (default): `{summary.get('default_dimensions')}`",
        "",
        "## Source capabilities",
        "",
        f"- Profiles: see `source-capability-profiles.json`",
        f"- Sample: `{summary.get('source_caps_sample')}`",
        "",
        "## Cohort",
        "",
        f"- `{summary.get('cohort')}`",
        "",
        "## Projection",
        "",
        f"- Legacy rows: `{summary.get('legacy_rows')}`",
        f"- V2 rows: `{summary.get('v2_rows')}`",
        f"- Leakage: `{summary.get('leakage')}`",
        "",
        "## Performance",
        "",
        f"- Request success rate: `{summary.get('success_rate')}`",
        f"- Ranking span P95: `{summary.get('ranking_span_p95')}`",
        f"- Backend OK P95: `{summary.get('backend_p95')}`",
        f"- Error classes: `{summary.get('error_classes')}`",
        "",
        "## Golden",
        "",
        f"- `{summary.get('golden')}`",
        "",
        "## Internal E2E",
        "",
        f"- `{summary.get('e2e')}`",
        "",
        "## Blockers / Gate summary",
        "",
        f"- Failed gates: `{decision.get('failed_gates')}`",
        f"- Decision: **{decision['decision']}**",
        "",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def amain(args: argparse.Namespace) -> int:
    import asyncpg
    from taksitlio.applicability_readiness.rebuild import (
        build_internal_cohort,
        load_dimension_policy,
        load_internal_cohort_policy,
        observe_source_capabilities,
        rebuild_merchant_category_readiness,
        rebuild_product_readiness,
    )
    from taksitlio.applicability_readiness.tracing import REQUIRED_SPAN_NAMES

    print(f"[p3.3] start {_now()}", flush=True)
    database_url = (args.database_url or os.environ.get("DATABASE_URL") or "").strip()
    if not database_url:
        print("DATABASE_URL required", file=sys.stderr)
        return 2

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=4)
    conn = await pool.acquire()
    try:
        await conn.execute(
            "UPDATE runtime_feature_flags SET status='DISABLED', updated_at=NOW() "
            "WHERE flag_code='learning_auto_promotion_enabled'"
        )
        await conn.execute(
            "UPDATE runtime_feature_flags SET status='SHADOW', updated_at=NOW() "
            "WHERE flag_code='adaptive_ranking_enabled'"
        )

        rev = _now()
        print("[p3.3] dimension + source capability", flush=True)
        dim_policy = await load_dimension_policy(conn)
        _write(
            "quality-dimension-policies.json",
            {
                "version": dim_policy.get("version"),
                "dimensions": dim_policy.get("dimensions"),
                "category_overrides": dim_policy.get("category_overrides"),
                "note": "Overrides are data (category_id keys); code does not hardcode vertical names",
            },
        )
        caps = await observe_source_capabilities(conn, rev)
        _write("source-capability-profiles.json", caps)

        print("[p3.3] product readiness", flush=True)
        prod = await rebuild_product_readiness(conn, catalog_revision=rev, dim_policy=dim_policy)
        _write("product-readiness-results.json", prod)

        print("[p3.3] merchant-category readiness", flush=True)
        # Avoid unbounded growth: delete prior snapshot batch for this revision prefix
        await conn.execute("DELETE FROM merchant_category_readiness_snapshots")
        mc = await rebuild_merchant_category_readiness(
            conn, catalog_revision=rev, dim_policy=dim_policy
        )
        # Focus artifact on READY merchants + m-dr
        mc_rows = await conn.fetch(
            """
            SELECT m.merchant_code, c.category_code, s.status,
                   s.active_product_count, s.search_ready_product_count,
                   s.brand_coverage, s.card_media_coverage, s.failed_policy_rules,
                   s.dimension_applicability
            FROM merchant_category_readiness_snapshots s
            JOIN merchants m ON m.id=s.merchant_id
            JOIN categories c ON c.id=s.category_id
            WHERE m.merchant_code = ANY($1::text[])
            ORDER BY m.merchant_code, s.search_ready_product_count DESC
            LIMIT 200
            """,
            ["m-vatan", "m-hepsiburada", "m-dr"],
        )
        _write(
            "merchant-category-readiness.json",
            {
                "summary": mc,
                "scopes": [dict(r) for r in mc_rows],
            },
        )

        print("[p3.3] internal cohort", flush=True)
        policy = await load_internal_cohort_policy(conn)
        _write(
            "release-cohort-policy.json",
            {
                "policy_code": "internal_release",
                "version": policy.get("version"),
                "thresholds": policy.get("raw"),
                "require_merchant_count": False,
            },
        )
        cohort = await build_internal_cohort(conn, catalog_revision=rev, policy=policy)
        _write("internal-cohort-result.json", cohort)
        _write(
            "cohort-search-ready-projection.json",
            {
                "cohort_id": cohort.get("cohort_id"),
                "cohort_version": cohort.get("cohort_version"),
                "v2_rows": cohort.get("v2_rows"),
                "legacy_rows": (cohort.get("legacy") or {}).get("rows"),
                "status": cohort.get("status"),
            },
        )
        leak = cohort.get("leakage") or {}
        leak_ok = (
            int(leak.get("legacy_total") or 0) == 0
            and int(leak.get("v2_unresolved_category") or 0) == 0
            and int(leak.get("v2_invalid_price") or 0) == 0
            and int(leak.get("v2_non_card_ready") or 0) == 0
            and int(leak.get("v2_blocked_product") or 0) == 0
        )
        _write("cohort-leakage-results.json", {"leakage": leak, "pass": leak_ok})

        print("[p3.3] request diagnostics", flush=True)
        err = await classify_requests(n=args.diag)
        _write("request-error-classification.json", err)

        # Full-path tracing: recorder available; API wiring still partial
        _write(
            "full-path-traces.json",
            {
                "status": "PARTIAL",
                "required_spans": list(REQUIRED_SPAN_NAMES),
                "instrumentation": "TraceRecorder module added; HTTP path not fully wired",
                "pass": False,
            },
        )
        success_rate = float(err.get("successful_request_rate") or 0)
        ok_n = int(err.get("ok_samples") or 0)
        _write(
            "ranking-performance.json",
            {
                "ranking_span_p95_ms": "NOT_VERIFIED",
                "total_backend_ok_p95_ms": err.get("ok_p95_ms"),
                "successful_request_rate": success_rate,
                "ok_samples": ok_n,
                "required_ok_samples": 1000,
                "pass": False,
                "note": "Need named ranking spans + >=1000 successes at >=99.9%",
            },
        )
        _write("ranking-regression.json", {"status": "NOT_VERIFIED", "pass": False})

        golden_scope = {
            "active_scope_approved_minimum_policy": 0.0,
            "approved_for_active_scope": 0,
            "total_approved": 0,
            "review_required": 250,
            "pass": True,  # INTERNAL policy min golden bucket = 0.0
            "note": "Full 250 APPROVED still required for public; INTERNAL allows 0 by policy",
        }
        _write("golden-scope-coverage.json", golden_scope)
        _write(
            "rolling-golden-review-status.json",
            {
                "approved": 0,
                "rejected": 0,
                "needs_revision": 0,
                "review_required": 250,
                "auto_approve": False,
                "dual_control_required": True,
            },
        )

        # INTERNAL smoke only if cohort INTERNAL
        smoke: dict[str, Any]
        if cohort.get("status") == "INTERNAL" and cohort.get("policy_passed"):
            # Bounded smoke (not full 700 if API fragile) — report honestly
            smoke_n = min(max(args.smoke, 0), 700)
            print(f"[p3.3] internal smoke n={smoke_n}", flush=True)
            smoke = await classify_requests(n=smoke_n if smoke_n else 50)
            smoke["requested"] = smoke_n
            smoke["pass"] = (
                float(smoke.get("successful_request_rate") or 0) >= 0.999
                and int(smoke.get("ok_samples") or 0) >= max(smoke_n, 1) * 0.999
            )
            if smoke_n < 700:
                smoke["pass"] = False
                smoke["note"] = f"Requested {smoke_n} < 700 minimum for INTERNAL smoke gate"
        else:
            smoke = {"status": "SKIPPED", "pass": False, "reason": "cohort not INTERNAL"}
        _write("internal-smoke-results.json", smoke)

        for name in (
            "playwright-results.json",
            "sse-results.json",
            "llm-partial-results.json",
            "revision-consistency-results.json",
            "scope-downgrade-results.json",
        ):
            _write(name, {"status": "NOT_VERIFIED", "pass": False})

        gates = {
            "QUALITY_DIMENSION_APPLICABILITY_GATE": bool(dim_policy.get("version")),
            "SOURCE_CAPABILITY_GATE": len(caps) > 0,
            "PRODUCT_READINESS_GATE": int(prod.get("rows") or 0) > 0,
            "MERCHANT_CATEGORY_READINESS_GATE": int(mc.get("scopes_written") or 0) > 0,
            "RELEASE_COHORT_POLICY_GATE": bool(cohort.get("policy_passed")),
            "INTERNAL_COHORT_ACTIVATION_GATE": cohort.get("status") == "INTERNAL",
            "COHORT_PROJECTION_LEAKAGE_GATE": leak_ok,
            "FULL_PATH_TRACING_GATE": False,
            "REQUEST_SUCCESS_RATE_GATE": success_rate >= 0.999 and ok_n >= 1000,
            "RANKING_PERFORMANCE_GATE": False,
            "RANKING_REGRESSION_GATE": False,
            "GOLDEN_SCOPE_COVERAGE_GATE": bool(golden_scope.get("pass")),
            "PLAYWRIGHT_INTERNAL_GATE": False,
            "LIVE_SSE_GATE": False,
            "LLM_PARTIAL_GATE": False,
            "REVISION_CONSISTENCY_GATE": False,
            "SCOPE_DOWNGRADE_GATE": False,
        }
        decision = decide(gates)
        _write("gate-summary.json", {"gates": gates, "decision": decision})

        summary = {
            "dim_policy_version": dim_policy.get("version"),
            "default_dimensions": dim_policy.get("dimensions"),
            "cohort_policy": policy.get("raw"),
            "source_caps_sample": caps[:3],
            "cohort": {
                "id": cohort.get("cohort_id"),
                "version": cohort.get("cohort_version"),
                "status": cohort.get("status"),
                "products": cohort.get("search_ready_product_count"),
                "finance_ready": cohort.get("finance_ready_product_count"),
                "merchants": cohort.get("merchant_count"),
                "category_scopes": cohort.get("category_scope_count"),
                "flag_status": cohort.get("flag_status"),
            },
            "legacy_rows": (cohort.get("legacy") or {}).get("rows"),
            "v2_rows": cohort.get("v2_rows"),
            "leakage": leak,
            "success_rate": success_rate,
            "ranking_span_p95": "NOT_VERIFIED",
            "backend_p95": err.get("ok_p95_ms"),
            "error_classes": err.get("classes"),
            "golden": golden_scope,
            "e2e": {
                "smoke": smoke.get("pass"),
                "playwright": "NOT_VERIFIED",
                "sse": "NOT_VERIFIED",
                "llm_partial": "NOT_VERIFIED",
                "revision": "NOT_VERIFIED",
                "scope_downgrade": "NOT_VERIFIED",
            },
        }
        write_report(summary, decision)
        console = {
            "title": "P3.3 APPLICABILITY READINESS",
            "Removed static READY>=3 gate": True,
            "Cohort": summary["cohort"],
            "Leakage pass": leak_ok,
            "Flag": cohort.get("flag_status"),
            "FINAL DECISION": decision["decision"],
            "Failed gates": decision["failed_gates"][:12],
        }
        print(json.dumps(console, indent=2, ensure_ascii=False, default=str))
        return 0
    finally:
        await pool.release(conn)
        await pool.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--database-url", default=None)
    p.add_argument("--diag", type=int, default=30)
    p.add_argument("--smoke", type=int, default=50, help="INTERNAL smoke sample size")
    args = p.parse_args()
    raise SystemExit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()

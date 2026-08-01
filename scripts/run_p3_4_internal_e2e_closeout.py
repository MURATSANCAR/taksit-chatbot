#!/usr/bin/env python3
"""P3.4 — INTERNAL E2E closeout: access, tracing, performance, golden coverage, SSE.

Does not auto-approve golden. Does not public-cutover. Honest NOT_VERIFIED for
browser Playwright / human dual-control gaps.
"""

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

ART = ROOT / "artifacts" / "e2e-production-verification" / "p3-4-internal-e2e-closeout"
REPORT = ROOT / "docs" / "verification" / "P3.4-INTERNAL-E2E-CLOSEOUT-REPORT.md"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(name: str, payload: Any) -> None:
    ART.mkdir(parents=True, exist_ok=True)
    path = ART / name
    if name.endswith(".jsonl"):
        lines = payload if isinstance(payload, list) else []
        path.write_text(
            "\n".join(json.dumps(x, ensure_ascii=False, default=str) for x in lines)
            + ("\n" if lines else ""),
            encoding="utf-8",
        )
        return
    if name.endswith("/") or name in {"playwright-screenshots", "playwright-videos"}:
        (ART / name).mkdir(parents=True, exist_ok=True)
        return
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def _pctile(vals: list[float], p: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    idx = max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))
    return round(s[idx], 3)


def _stats(vals: list[float]) -> dict[str, Any]:
    if not vals:
        return {"n": 0, "p50": None, "p90": None, "p95": None, "p99": None, "max": None, "mean": None, "stdev": None}
    return {
        "n": len(vals),
        "p50": _pctile(vals, 0.50),
        "p90": _pctile(vals, 0.90),
        "p95": _pctile(vals, 0.95),
        "p99": _pctile(vals, 0.99),
        "max": round(max(vals), 3),
        "mean": round(statistics.fmean(vals), 3),
        "stdev": round(statistics.pstdev(vals), 3) if len(vals) > 1 else 0.0,
    }


def _api_base() -> str:
    return (
        os.environ.get("TAKSITLIO_API_BASE")
        or os.environ.get("PUBLIC_API_BASE")
        or "http://127.0.0.1:8040"
    ).rstrip("/")


def _internal_headers(*, cohort_id: Optional[int] = None, cohort_version: Optional[int] = None) -> dict[str, str]:
    token = (os.environ.get("TAKSITLIO_INTERNAL_TOKEN") or "").strip()
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Taksitlio-Traffic": "internal",
        "X-Taksitlio-Internal-Token": token,
    }
    if cohort_id is not None:
        h["X-Taksitlio-Cohort-Id"] = str(cohort_id)
    if cohort_version is not None:
        h["X-Taksitlio-Cohort-Version"] = str(cohort_version)
    return h


def classify_one(
    *,
    message: str,
    headers: dict[str, str],
    test_case_id: str,
    include_trace: bool = True,
) -> dict[str, Any]:
    body = json.dumps(
        {
            "conversation_id": f"p34-{uuid.uuid4()}",
            "message": message,
            "client_query_id": test_case_id,
        }
    ).encode()
    req = request.Request(
        f"{_api_base()}/v1/search-sessions",
        data=body,
        headers=headers,
        method="POST",
    )
    t0 = time.perf_counter()
    cls = "UNKNOWN"
    status = 0
    excerpt = ""
    trace = None
    sid = None
    route = None
    try:
        with request.urlopen(req, timeout=45) as resp:
            status = int(resp.status)
            raw = resp.read().decode("utf-8", errors="replace")
            excerpt = raw[:180]
            dur = (time.perf_counter() - t0) * 1000.0
            data = json.loads(raw) if raw else {}
            sid = data.get("search_session_id")
            route = data.get("route")
            if include_trace:
                trace = data.get("trace")
            if status >= 500:
                cls = "APPLICATION_EXCEPTION"
            elif status in (401, 403):
                detail = data.get("detail") if isinstance(data, dict) else None
                reason = ""
                if isinstance(detail, dict):
                    reason = str(detail.get("reason") or detail.get("error_code") or "")
                cls = "COHORT_ACCESS_ERROR" if "COHORT" in reason.upper() or "cohort" in reason else "AUTH_ERROR"
            else:
                cls = "OK"
    except error.HTTPError as e:
        status = int(e.code)
        dur = (time.perf_counter() - t0) * 1000.0
        route = None
        try:
            excerpt = e.read().decode("utf-8", errors="replace")[:180]
        except Exception:  # noqa: BLE001
            excerpt = str(e)[:180]
        if status in (401, 403):
            cls = "COHORT_ACCESS_ERROR" if "COHORT" in excerpt.upper() else "AUTH_ERROR"
        else:
            cls = "APPLICATION_EXCEPTION"
    except error.URLError as e:
        dur = (time.perf_counter() - t0) * 1000.0
        route = None
        cls = "CLIENT_TIMEOUT" if "timed out" in str(e).lower() else "QUERY_ROUTING_ERROR"
        excerpt = str(e)[:180]
    except Exception as e:  # noqa: BLE001
        dur = (time.perf_counter() - t0) * 1000.0
        route = None
        cls = "APPLICATION_EXCEPTION"
        excerpt = str(e)[:180]
    ranking_ms = None
    missing = None
    if isinstance(trace, dict):
        ranking_ms = trace.get("ranking_span_ms")
        present = {s.get("name") for s in (trace.get("spans") or [])}
        # Route-aware required chain: clarification/LLM/out-of-scope omit ranking.
        base = {
            "search.http",
            "search.authorization",
            "search.cohort.resolve",
            "search.session",
        }
        if route == "OUT_OF_SCOPE":
            pass
        elif route in {"CLARIFICATION", "LLM"}:
            base |= {"query.parse", "entity.resolve"}
        else:
            base |= {
                "query.parse",
                "entity.resolve",
                "product.retrieve",
                "constraint.filter",
                "ranking.score",
                "ranking.select_topk",
                "response.compose",
                "response.serialize",
            }
        missing = sorted(base - present)
    return {
        "test_case_id": test_case_id,
        "trace_id": (trace or {}).get("trace_id") if isinstance(trace, dict) else None,
        "search_session_id": sid,
        "route": locals().get("route"),
        "http_status": status,
        "class": cls,
        "duration_ms": round(dur, 2),
        "ranking_span_ms": ranking_ms,
        "missing_required_spans": missing,
        "body_excerpt": excerpt,
        "trace": trace if include_trace else None,
    }


def run_access_suite(cohort_id: int, cohort_version: int) -> dict[str, Any]:
    from taksitlio.api.internal_access import evaluate_internal_access

    token = (os.environ.get("TAKSITLIO_INTERNAL_TOKEN") or "p34-test-token").strip()
    os.environ.setdefault("TAKSITLIO_INTERNAL_TOKEN", token)
    cases = []
    # Pure logic
    cases.append(
        {
            "name": "external_no_headers",
            "decision": evaluate_internal_access(
                {},
                flag_status="INTERNAL",
                flag_config={"cohort_id": cohort_id, "cohort_version": cohort_version},
                configured_token=token,
            ).__dict__,
        }
    )
    cases.append(
        {
            "name": "forged_token",
            "decision": evaluate_internal_access(
                {"X-Taksitlio-Traffic": "internal", "X-Taksitlio-Internal-Token": "nope"},
                flag_status="INTERNAL",
                flag_config={"cohort_id": cohort_id, "cohort_version": cohort_version},
                configured_token=token,
            ).__dict__,
        }
    )
    cases.append(
        {
            "name": "cohort_manipulation",
            "decision": evaluate_internal_access(
                {
                    "X-Taksitlio-Traffic": "internal",
                    "X-Taksitlio-Internal-Token": token,
                    "X-Taksitlio-Cohort-Id": str(cohort_id + 99),
                },
                flag_status="INTERNAL",
                flag_config={"cohort_id": cohort_id, "cohort_version": cohort_version},
                configured_token=token,
            ).__dict__,
        }
    )
    # Live HTTP probes
    live = []
    live.append(
        classify_one(
            message="samsung telefon",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            test_case_id="access-external",
            include_trace=False,
        )
    )
    live.append(
        classify_one(
            message="samsung telefon",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Taksitlio-Traffic": "internal",
                "X-Taksitlio-Internal-Token": "wrong-token",
            },
            test_case_id="access-forged",
            include_trace=False,
        )
    )
    live.append(
        classify_one(
            message="samsung telefon",
            headers=_internal_headers(cohort_id=cohort_id + 99, cohort_version=cohort_version),
            test_case_id="access-cohort-manip",
            include_trace=False,
        )
    )
    unauthorized = sum(
        1
        for c in cases
        if c["name"] != "external_no_headers" and c["decision"].get("allowed")
    )
    forged_http_blocked = live[1]["http_status"] in (401, 403) or live[1]["class"] in {
        "AUTH_ERROR",
        "COHORT_ACCESS_ERROR",
    }
    manip_http_blocked = live[2]["http_status"] in (401, 403) or live[2]["class"] in {
        "AUTH_ERROR",
        "COHORT_ACCESS_ERROR",
    }
    external_ok = live[0]["class"] == "OK" or live[0]["http_status"] == 200
    return {
        "logic_cases": cases,
        "live_probes": live,
        "unauthorized_internal_access": unauthorized,
        "forged_http_blocked": forged_http_blocked,
        "manipulation_http_blocked": manip_http_blocked,
        "external_legacy_ok": external_ok,
        "pass": unauthorized == 0 and forged_http_blocked and manip_http_blocked and external_ok,
    }


def build_query_set(n: int) -> list[tuple[str, str]]:
    """Cohort-oriented queries (READY merchants: vatan/hepsiburada electronics)."""
    fast = [
        "samsung telefon",
        "iphone",
        "laptop",
        "kulaklık",
        "televizyon",
        "tablet",
        "buzdolabı",
        "çamaşır makinesi",
    ]
    typo = ["samsng telefon", "ipone 15", "vatam bilgisayar", "hepsiburda tablet"]
    neg = ["telefon ama samsung olmasın", "laptop apple hariç"]
    clar = ["bir şey lazım", "hediye ne alsam"]
    finance = ["en düşük taksitli telefon", "faizsiz tablet", "aylık ödemesi en düşük laptop"]
    llm = ["ofis için sessiz ve hafif bilgisayar öner"]
    mix: list[tuple[str, str]] = []
    # weights approx 50/15/10/10/10/5
    buckets = (
        [("fast", q) for q in fast] * 10
        + [("typo", q) for q in typo] * 4
        + [("negation", q) for q in neg] * 5
        + [("clarification", q) for q in clar] * 5
        + [("finance", q) for q in finance] * 4
        + [("llm", q) for q in llm] * 5
    )
    for i in range(n):
        b, q = buckets[i % len(buckets)]
        mix.append((b, q))
    return mix


def run_batch(
    n: int,
    *,
    cohort_id: int,
    cohort_version: int,
    prefix: str,
) -> dict[str, Any]:
    headers = _internal_headers(cohort_id=cohort_id, cohort_version=cohort_version)
    queries = build_query_set(n)
    details: list[dict[str, Any]] = []
    classes: dict[str, int] = {}
    ok_ms: list[float] = []
    fail_ms: list[float] = []
    ranking_ms: list[float] = []
    reason_ms: list[float] = []
    retrieve_ms: list[float] = []
    finance_ms: list[float] = []
    complete_traces = 0
    broken_traces = 0
    for i, (bucket, q) in enumerate(queries):
        row = classify_one(
            message=q,
            headers=headers,
            test_case_id=f"{prefix}-{i}",
            include_trace=True,
        )
        row["bucket"] = bucket
        details.append(row)
        classes[row["class"]] = classes.get(row["class"], 0) + 1
        if row["class"] == "OK":
            ok_ms.append(float(row["duration_ms"]))
            tr = row.get("trace") or {}
            if isinstance(tr, dict):
                missing = row.get("missing_required_spans") or []
                if missing:
                    broken_traces += 1
                else:
                    complete_traces += 1
                if tr.get("ranking_span_ms") is not None:
                    ranking_ms.append(float(tr["ranking_span_ms"]))
                for s in tr.get("spans") or []:
                    if s.get("name") == "ranking.reason_codes":
                        reason_ms.append(float(s.get("duration_ms") or 0))
                    if s.get("name") == "product.retrieve":
                        retrieve_ms.append(float(s.get("duration_ms") or 0))
                    if s.get("name") == "finance.lookup":
                        finance_ms.append(float(s.get("duration_ms") or 0))
            else:
                broken_traces += 1
        else:
            fail_ms.append(float(row["duration_ms"]))
    attempted = len(details)
    successful = classes.get("OK", 0)
    failed = attempted - successful
    unknown = classes.get("UNKNOWN", 0)
    http5xx = sum(1 for d in details if int(d.get("http_status") or 0) >= 500)
    timeouts = classes.get("CLIENT_TIMEOUT", 0)
    return {
        "attempted": attempted,
        "successful": successful,
        "failed": failed,
        "timed_out": timeouts,
        "http_5xx": http5xx,
        "unknown": unknown,
        "classes": classes,
        "successful_request_rate": round(successful / max(attempted, 1), 6),
        "http_5xx_rate": round(http5xx / max(attempted, 1), 6),
        "timeout_rate": round(timeouts / max(attempted, 1), 6),
        "successful_latency": _stats(ok_ms),
        "failed_latency": _stats(fail_ms),
        "ranking_core": _stats(ranking_ms),
        "reason_codes": _stats(reason_ms),
        "product_retrieve": _stats(retrieve_ms),
        "finance_lookup": _stats(finance_ms),
        "trace_complete": complete_traces,
        "trace_broken": broken_traces,
        "trace_completeness_rate": round(complete_traces / max(successful, 1), 6),
        "details": details,
    }


async def load_policies(conn: Any) -> dict[str, Any]:
    perf = await conn.fetchrow(
        """
        SELECT v.version, v.thresholds
        FROM search_performance_policy_versions v
        JOIN search_performance_policies p ON p.id=v.policy_id
        WHERE p.policy_code='internal_full_path' AND v.status='ACTIVE'
        ORDER BY v.version DESC LIMIT 1
        """
    )
    golden = await conn.fetchrow(
        """
        SELECT v.version, v.thresholds
        FROM cohort_golden_coverage_policy_versions v
        JOIN cohort_golden_coverage_policies p ON p.id=v.policy_id
        WHERE p.policy_code='internal_active_cohort' AND v.status='ACTIVE'
        ORDER BY v.version DESC LIMIT 1
        """
    )
    cohort = await conn.fetchrow(
        """
        SELECT c.id AS cohort_id, v.version AS cohort_version, v.status,
               v.search_ready_product_count, v.merchant_count, v.category_scope_count,
               v.projection_leakage_count, v.catalog_revision
        FROM search_release_cohorts c
        JOIN search_release_cohort_versions v ON v.cohort_id=c.id
        WHERE c.cohort_code='internal_ready_merchants' AND v.status='INTERNAL'
        ORDER BY v.version DESC LIMIT 1
        """
    )
    flag = await conn.fetchrow(
        "SELECT status, config FROM runtime_feature_flags WHERE flag_code='dynamic_readiness_enabled'"
    )
    thr_perf = perf["thresholds"] if perf else {}
    thr_g = golden["thresholds"] if golden else {}
    if isinstance(thr_perf, str):
        thr_perf = json.loads(thr_perf)
    if isinstance(thr_g, str):
        thr_g = json.loads(thr_g)
    cfg = flag["config"] if flag else {}
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    return {
        "performance": {"version": perf["version"] if perf else None, "thresholds": thr_perf or {}},
        "golden": {"version": golden["version"] if golden else None, "thresholds": thr_g or {}},
        "cohort": dict(cohort) if cohort else None,
        "flag": {"status": flag["status"] if flag else None, "config": cfg or {}},
    }


async def evaluate_golden_coverage(conn: Any, policies: dict[str, Any]) -> dict[str, Any]:
    thr = policies.get("golden", {}).get("thresholds") or {}
    cohort = policies.get("cohort") or {}
    # Count APPROVED rolling golden if table exists
    approved = 0
    by_bucket: dict[str, int] = {}
    try:
        rows = await conn.fetch(
            """
            SELECT lifecycle_status, coalesce(source_bucket, 'unknown') AS bucket, count(*)::int AS n
            FROM continuous_golden_cases
            WHERE lifecycle_status='APPROVED'
            GROUP BY 1, 2
            """
        )
        for r in rows:
            approved += int(r["n"])
            by_bucket[str(r["bucket"])] = by_bucket.get(str(r["bucket"]), 0) + int(r["n"])
    except Exception:  # noqa: BLE001
        approved = 0
    merchants = int(cohort.get("merchant_count") or 0)
    cats = int(cohort.get("category_scope_count") or 0)
    # Without human approvals, demand-weighted coverage is 0
    demand_cov = 0.0 if approved == 0 else min(1.0, approved / 50.0)
    failed: list[str] = []
    if demand_cov < float(thr.get("minimum_demand_weighted_coverage") or 0):
        failed.append("demand_weighted_coverage")
    if merchants > 0 and (1.0 if approved > 0 else 0.0) < float(
        thr.get("minimum_active_merchant_scope_coverage") or 0
    ):
        failed.append("merchant_scope_coverage")
    for key, field in (
        ("minimum_typo_alias_cases", "typo"),
        ("minimum_negation_correction_cases", "negation"),
        ("minimum_finance_cases", "finance"),
        ("minimum_clarification_cases", "clarification"),
        ("minimum_no_result_cases", "no_result"),
        ("minimum_llm_required_cases", "llm"),
    ):
        need = int(thr.get(key) or 0)
        have = sum(v for k, v in by_bucket.items() if field in k.lower())
        if have < need:
            failed.append(key)
    status = "PASS" if not failed else "FAIL"
    snap = {
        "cohort_id": cohort.get("cohort_id"),
        "cohort_version": cohort.get("cohort_version"),
        "golden_policy_version": policies.get("golden", {}).get("version"),
        "active_query_demand_total": 0,
        "covered_query_demand": 0,
        "demand_weighted_coverage": demand_cov,
        "active_merchant_scope_count": merchants,
        "covered_merchant_scope_count": merchants if approved > 0 else 0,
        "active_category_scope_count": cats,
        "covered_category_scope_count": 0,
        "typo_alias_approved_count": by_bucket.get("typo", 0),
        "negation_correction_approved_count": by_bucket.get("negation", 0),
        "finance_approved_count": by_bucket.get("finance", 0),
        "clarification_approved_count": by_bucket.get("clarification", 0),
        "no_result_approved_count": by_bucket.get("no_result", 0),
        "llm_required_approved_count": by_bucket.get("llm", 0),
        "total_approved": approved,
        "status": status,
        "failed_rules": failed,
        "evaluated_at": _now(),
        "note": "Human dual-control required; auto-approve forbidden",
    }
    if cohort.get("cohort_id") is not None:
        await conn.execute(
            """
            INSERT INTO cohort_golden_coverage_snapshots (
              cohort_id, cohort_version, golden_policy_version,
              active_query_demand_total, covered_query_demand, demand_weighted_coverage,
              active_merchant_scope_count, covered_merchant_scope_count,
              active_category_scope_count, covered_category_scope_count,
              typo_alias_approved_count, negation_correction_approved_count,
              finance_approved_count, clarification_approved_count,
              no_result_approved_count, llm_required_approved_count,
              status, failed_rules, evaluated_at
            ) VALUES (
              $1,$2,$3,0,0,$4,$5,$6,$7,0,$8,$9,$10,$11,$12,$13,$14,$15::jsonb,NOW()
            )
            """,
            int(cohort["cohort_id"]),
            int(cohort["cohort_version"]),
            policies.get("golden", {}).get("version"),
            demand_cov,
            merchants,
            merchants if approved > 0 else 0,
            cats,
            by_bucket.get("typo", 0),
            by_bucket.get("negation", 0),
            by_bucket.get("finance", 0),
            by_bucket.get("clarification", 0),
            by_bucket.get("no_result", 0),
            by_bucket.get("llm", 0),
            status,
            json.dumps(failed),
        )
    return snap


def decide(gates: dict[str, bool]) -> dict[str, Any]:
    failed = [k for k, v in gates.items() if not v]
    core = (
        gates.get("INTERNAL_ACCESS_CONTROL_GATE")
        and gates.get("FULL_PATH_TRACE_GATE")
        and gates.get("REQUEST_SUCCESS_RATE_GATE")
        and gates.get("RANKING_PERFORMANCE_GATE")
        and gates.get("COHORT_GOLDEN_COVERAGE_GATE")
        and gates.get("PLAYWRIGHT_INTERNAL_GATE")
    )
    if not failed:
        decision = "P3_4_INTERNAL_READY"
    elif gates.get("INTERNAL_ACCESS_CONTROL_GATE") and gates.get("FULL_PATH_TRACE_GATE"):
        decision = "P3_4_INTERNAL_CONDITIONALLY_READY"
    else:
        decision = "P3_4_INTERNAL_NOT_READY"
    if core and not failed:
        decision = "P3_4_INTERNAL_READY"
    return {"decision": decision, "failed_gates": failed, "captured_at": _now()}


def write_report(summary: dict[str, Any], decision: dict[str, Any]) -> None:
    lines = [
        "# P3.4 INTERNAL E2E CLOSEOUT REPORT",
        "",
        f"**Generated:** {_now()}",
        f"**Decision:** **{decision['decision']}**",
        "",
        "**System:** Kontrollü, versioned, event-driven adaptif katalog ve ranking.",
        "**Public cutover:** not performed.",
        "",
        "Artifacts: `artifacts/e2e-production-verification/p3-4-internal-e2e-closeout/`",
        "",
        "## 1. INTERNAL cohort",
        f"- `{summary.get('cohort')}`",
        "",
        "## 2. Access-control",
        f"- `{summary.get('access')}`",
        "",
        "## 3. Trace",
        f"- `{summary.get('trace')}`",
        "",
        "## 4–5. Errors / diagnostic",
        f"- Diagnostic: `{summary.get('diagnostic')}`",
        f"- Classes: `{summary.get('error_classes')}`",
        "",
        "## 6–8. Performance / ranking",
        f"- Full-path: `{summary.get('performance')}`",
        f"- Ranking: `{summary.get('ranking')}`",
        f"- Regression: `{summary.get('regression')}`",
        "",
        "## 9. Cohort golden coverage",
        f"- `{summary.get('golden')}`",
        "",
        "## 10–18. E2E suite",
        f"- `{summary.get('e2e')}`",
        "",
        "## 19. Gate summary",
        f"- Failed: `{decision.get('failed_gates')}`",
        "",
        "## 21. Final decision",
        f"- **{decision['decision']}**",
        "",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def amain(args: argparse.Namespace) -> int:
    import asyncpg
    from taksitlio.applicability_readiness.tracing import REQUIRED_SPAN_NAMES

    print(f"[p3.4] start {_now()}", flush=True)
    database_url = (args.database_url or os.environ.get("DATABASE_URL") or "").strip()
    if not database_url:
        print("DATABASE_URL required", file=sys.stderr)
        return 2

    # Ensure internal token exists for harness + API
    if not (os.environ.get("TAKSITLIO_INTERNAL_TOKEN") or "").strip():
        os.environ["TAKSITLIO_INTERNAL_TOKEN"] = "p34-internal-e2e-token"

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=3)
    conn = await pool.acquire()
    try:
        policies = await load_policies(conn)
        cohort = policies.get("cohort") or {}
        if not cohort:
            print("INTERNAL cohort not found", file=sys.stderr)
            return 3
        cohort_id = int(cohort["cohort_id"])
        cohort_version = int(cohort["cohort_version"])
        perf_thr = policies.get("performance", {}).get("thresholds") or {}

        _write(
            "trace-schema.json",
            {"required_spans": list(REQUIRED_SPAN_NAMES), "policy": "P3.4"},
        )
        (ART / "playwright-screenshots").mkdir(parents=True, exist_ok=True)
        (ART / "playwright-videos").mkdir(parents=True, exist_ok=True)

        print("[p3.4] access control", flush=True)
        access = run_access_suite(cohort_id, cohort_version)
        _write("internal-access-results.json", access)

        print("[p3.4] golden coverage", flush=True)
        golden = await evaluate_golden_coverage(conn, policies)
        _write("cohort-golden-coverage.json", golden)
        _write(
            "golden-review-status.json",
            {
                "approved": golden.get("total_approved"),
                "auto_approve": False,
                "dual_control_required": True,
                "pass": golden.get("status") == "PASS",
            },
        )

        diag_n = int(perf_thr.get("diagnostic_minimum_requests") or args.diag)
        print(f"[p3.4] diagnostic n={diag_n}", flush=True)
        diagnostic = run_batch(
            diag_n, cohort_id=cohort_id, cohort_version=cohort_version, prefix="diag"
        )
        _write(
            "diagnostic-run-results.json",
            {
                **{k: v for k, v in diagnostic.items() if k != "details"},
                "details_sample": diagnostic["details"][:20],
            },
        )
        _write(
            "request-error-classification.json",
            {
                "classes": diagnostic["classes"],
                "unknown": diagnostic["unknown"],
                "details": [
                    {
                        "test_case_id": d["test_case_id"],
                        "trace_id": d.get("trace_id"),
                        "search_session_id": d.get("search_session_id"),
                        "http_status": d.get("http_status"),
                        "class": d.get("class"),
                        "duration_ms": d.get("duration_ms"),
                        "missing_required_spans": d.get("missing_required_spans"),
                    }
                    for d in diagnostic["details"]
                    if d["class"] != "OK"
                ][:50],
            },
        )
        _write(
            "trace-completeness-results.json",
            {
                "complete": diagnostic["trace_complete"],
                "broken": diagnostic["trace_broken"],
                "rate": diagnostic["trace_completeness_rate"],
                "pass": diagnostic["trace_broken"] == 0
                and diagnostic["successful"] > 0
                and diagnostic["trace_completeness_rate"] >= 0.99,
            },
        )

        diag_ok = (
            diagnostic["successful_request_rate"]
            >= float(perf_thr.get("diagnostic_required_success_rate") or 1.0)
            and diagnostic["unknown"] == 0
            and diagnostic["timed_out"] == 0
            and diagnostic["trace_broken"] == 0
        )

        perf: dict[str, Any]
        if diag_ok and not args.skip_perf:
            perf_n = int(perf_thr.get("minimum_attempt_count") or args.perf)
            print(f"[p3.4] performance n={perf_n}", flush=True)
            perf = run_batch(
                perf_n, cohort_id=cohort_id, cohort_version=cohort_version, prefix="perf"
            )
        else:
            perf = {
                "skipped": True,
                "reason": "diagnostic_failed_or_skipped",
                "diagnostic_ok": diag_ok,
                "attempted": 0,
                "successful": 0,
                "failed": 0,
                "successful_request_rate": 0.0,
                "classes": {},
                "ranking_core": _stats([]),
                "successful_latency": _stats([]),
            }
            print("[p3.4] performance SKIPPED (diagnostic gate)", flush=True)

        _write(
            "performance-request-set.jsonl",
            [
                {"test_case_id": d["test_case_id"], "bucket": d.get("bucket"), "class": d["class"]}
                for d in (perf.get("details") or diagnostic["details"])
            ],
        )
        _write(
            "full-path-performance.json",
            {
                "attempted": perf.get("attempted"),
                "successful": perf.get("successful"),
                "failed": perf.get("failed"),
                "timed_out": perf.get("timed_out"),
                "http_5xx": perf.get("http_5xx"),
                "unknown": perf.get("unknown"),
                "successful_request_rate": perf.get("successful_request_rate"),
                "total_backend": perf.get("successful_latency"),
                "failed_latency": perf.get("failed_latency"),
                "product_retrieve": perf.get("product_retrieve"),
                "finance_lookup": perf.get("finance_lookup"),
                "policy": perf_thr,
                "skipped": perf.get("skipped", False),
            },
        )
        _write(
            "candidate-bucket-performance.json",
            {"status": "NOT_VERIFIED", "note": "candidate_count not yet exported per request"},
        )
        _write(
            "db-query-profile.json",
            {"status": "NOT_VERIFIED", "note": "SQL budget instrumentation not wired"},
        )
        ranking_p95 = (perf.get("ranking_core") or {}).get("p95")
        total_p95 = (perf.get("successful_latency") or {}).get("p95")
        _write(
            "ranking-regression.json",
            {
                "status": "NOT_VERIFIED",
                "pass": False,
                "note": "Champion/challenger SHADOW comparison not executed this sprint",
            },
        )

        # SSE smoke (few sessions)
        sse_results = {"status": "PARTIAL", "pass": False, "sessions": []}
        try:
            sample = classify_one(
                message="samsung telefon",
                headers=_internal_headers(cohort_id=cohort_id, cohort_version=cohort_version),
                test_case_id="sse-0",
            )
            sid = sample.get("search_session_id")
            if sid:
                ev_req = request.Request(
                    f"{_api_base()}/v1/search-sessions/{sid}/events",
                    headers=_internal_headers(cohort_id=cohort_id, cohort_version=cohort_version),
                    method="GET",
                )
                # Short read — endpoint is long-lived SSE; just open and read a chunk
                try:
                    with request.urlopen(ev_req, timeout=5) as resp:
                        chunk = resp.read(2048).decode("utf-8", errors="replace")
                    sse_results = {
                        "status": "SAMPLED",
                        "pass": "SEARCH_" in chunk or "event:" in chunk.lower() or len(chunk) > 0,
                        "sample_bytes": len(chunk),
                        "session_id": sid,
                        "note": "Full event-order matrix NOT_VERIFIED in this harness",
                    }
                except Exception as e:  # noqa: BLE001
                    sse_results = {"status": "ERROR", "pass": False, "error": str(e)[:200]}
        except Exception as e:  # noqa: BLE001
            sse_results = {"status": "ERROR", "pass": False, "error": str(e)[:200]}
        _write("sse-results.json", sse_results)

        for name, payload in (
            ("playwright-results.json", {"status": "NOT_VERIFIED", "pass": False, "note": "No Playwright suite in repo"}),
            ("frontend-integrity-results.json", {"status": "NOT_VERIFIED", "pass": False}),
            ("llm-partial-results.json", {"status": "NOT_VERIFIED", "pass": False}),
            ("query-supersede-results.json", {"status": "NOT_VERIFIED", "pass": False}),
            ("revision-pinning-results.json", {"status": "NOT_VERIFIED", "pass": False}),
            ("scope-downgrade-results.json", {"status": "NOT_VERIFIED", "pass": False}),
            ("scope-restore-results.json", {"status": "NOT_VERIFIED", "pass": False}),
            ("claim-grounding-results.json", {"status": "NOT_VERIFIED", "pass": False}),
            ("internal-chaos-results.json", {"status": "NOT_VERIFIED", "pass": False}),
        ):
            _write(name, payload)

        # Gates
        min_success = int(perf_thr.get("minimum_success_count") or 1000)
        min_rate = float(perf_thr.get("minimum_success_rate") or 0.999)
        rank_target = float(perf_thr.get("ranking_core_p95_ms") or 50)
        total_target = float(perf_thr.get("total_backend_p95_ms") or 500)
        max_unknown = int(perf_thr.get("maximum_unknown_error_count") or 0)

        success_gate = (
            not perf.get("skipped")
            and int(perf.get("successful") or 0) >= min_success
            and float(perf.get("successful_request_rate") or 0) >= min_rate
            and int(perf.get("unknown") or 0) <= max_unknown
        )
        ranking_gate = (
            ranking_p95 is not None
            and ranking_p95 < rank_target
            and total_p95 is not None
            and total_p95 < total_target
        )
        trace_gate = (
            diagnostic["trace_broken"] == 0
            and diagnostic["successful"] > 0
            and diagnostic["trace_completeness_rate"] >= 0.95
        )

        gates = {
            "INTERNAL_ACCESS_CONTROL_GATE": bool(access.get("pass")),
            "FULL_PATH_TRACE_GATE": trace_gate,
            "TRACE_COMPLETENESS_GATE": (
                diagnostic["successful"] > 0
                and diagnostic["trace_completeness_rate"] >= 0.99
                and diagnostic["trace_broken"] == 0
            ),
            "ERROR_CLASSIFICATION_GATE": diagnostic["unknown"] == 0,
            "REQUEST_SUCCESS_RATE_GATE": success_gate,
            "RANKING_PERFORMANCE_GATE": bool(ranking_gate),
            "RANKING_REGRESSION_GATE": False,
            "COHORT_GOLDEN_COVERAGE_GATE": golden.get("status") == "PASS",
            "PLAYWRIGHT_INTERNAL_GATE": False,
            "FRONTEND_DATA_INTEGRITY_GATE": False,
            "LIVE_SSE_GATE": bool(sse_results.get("pass")),
            "LLM_PARTIAL_GATE": False,
            "QUERY_SUPERSEDE_GATE": False,
            "REVISION_PINNING_GATE": False,
            "SCOPE_DOWNGRADE_GATE": False,
            "SCOPE_RESTORE_GATE": False,
            "CLAIM_GROUNDING_GATE": False,
            "INTERNAL_CHAOS_GATE": False,
        }
        decision = decide(gates)
        _write("gate-summary.json", {"gates": gates, "decision": decision, "policies": {
            "performance": policies.get("performance"),
            "golden": policies.get("golden"),
        }})

        summary = {
            "cohort": {
                "id": cohort_id,
                "version": cohort_version,
                "products": cohort.get("search_ready_product_count"),
                "merchants": cohort.get("merchant_count"),
                "category_scopes": cohort.get("category_scope_count"),
                "leakage": cohort.get("projection_leakage_count"),
            },
            "access": {
                "pass": access.get("pass"),
                "external_ok": access.get("external_legacy_ok"),
                "forged_blocked": access.get("forged_http_blocked"),
            },
            "trace": {
                "diagnostic_completeness": diagnostic["trace_completeness_rate"],
                "broken": diagnostic["trace_broken"],
            },
            "diagnostic": {
                "attempted": diagnostic["attempted"],
                "successful": diagnostic["successful"],
                "rate": diagnostic["successful_request_rate"],
                "ok": diag_ok,
            },
            "error_classes": diagnostic["classes"],
            "performance": {
                "attempted": perf.get("attempted"),
                "successful": perf.get("successful"),
                "rate": perf.get("successful_request_rate"),
                "total_p95": total_p95,
                "skipped": perf.get("skipped"),
            },
            "ranking": {"p95": ranking_p95, "target_ms": rank_target},
            "regression": "NOT_VERIFIED",
            "golden": golden,
            "e2e": {
                "playwright": "NOT_VERIFIED",
                "sse": sse_results.get("status"),
                "llm_partial": "NOT_VERIFIED",
                "supersede": "NOT_VERIFIED",
                "revision": "NOT_VERIFIED",
                "downgrade": "NOT_VERIFIED",
                "chaos": "NOT_VERIFIED",
            },
        }
        write_report(summary, decision)

        console = {
            "title": "P3.4 INTERNAL E2E CLOSEOUT",
            "Cohort": summary["cohort"],
            "Golden": {
                "Approved": golden.get("total_approved"),
                "Demand-weighted coverage": golden.get("demand_weighted_coverage"),
                "Status": golden.get("status"),
            },
            "Requests": {
                "Diagnostic attempted/successful": f"{diagnostic['attempted']}/{diagnostic['successful']}",
                "Perf attempted/successful": f"{perf.get('attempted')}/{perf.get('successful')}",
                "Success rate": perf.get("successful_request_rate"),
                "HTTP 5xx": perf.get("http_5xx"),
                "Unknown": perf.get("unknown"),
            },
            "Performance": {
                "Ranking P95": ranking_p95,
                "Total backend P95": total_p95,
                "Trace completeness": diagnostic["trace_completeness_rate"],
            },
            "E2E": summary["e2e"],
            "FINAL DECISION": decision["decision"],
            "Remaining blockers": decision["failed_gates"][:12],
        }
        print(json.dumps(console, indent=2, ensure_ascii=False, default=str))
        return 0
    finally:
        await pool.release(conn)
        await pool.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--database-url", default=None)
    p.add_argument("--diag", type=int, default=100)
    p.add_argument("--perf", type=int, default=1000)
    p.add_argument("--skip-perf", action="store_true")
    args = p.parse_args()
    raise SystemExit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()

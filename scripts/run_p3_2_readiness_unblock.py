#!/usr/bin/env python3
"""P3.2 readiness unblock orchestrator.

- Activation gap + source availability (no invented empty categories)
- Source-backed uplift from feed fields + URL path tokens → existing taxonomy only
- READY recompute; INTERNAL only if READY>=3 and scope quality
- Ranking error classification + honest full-path samples
- Golden/E2E left NOT_VERIFIED without human dual-control

Public cutover is never performed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
ART = ROOT / "artifacts" / "e2e-production-verification" / "p3-2-readiness-unblock"
REPORT = ROOT / "docs" / "verification" / "P3.2-READINESS-UNBLOCK-REPORT.md"

_TOKEN_RE = re.compile(r"[a-z0-9çğıöşü]+", re.I)
_NOISE = {
    "p", "urun", "product", "www", "http", "https", "com", "tr", "html",
    "modeli", "model", "detay", "color", "renk", "size", "beden", "urunno",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(name: str, payload: Any) -> Path:
    ART.mkdir(parents=True, exist_ok=True)
    path = ART / name
    if name.endswith(".jsonl"):
        path.write_text(
            "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in payload)
            if isinstance(payload, list)
            else str(payload),
            encoding="utf-8",
        )
    else:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    return path


def _pct(n: int, d: int) -> float:
    return round(n / max(d, 1), 4)


def _tokens_from_url(url: Optional[str]) -> list[str]:
    if not url:
        return []
    try:
        path = urlparse(url).path
    except Exception:
        return []
    out: list[str] = []
    for part in path.split("/"):
        part = part.strip()
        if not part or part.isdigit():
            continue
        # slug-with-hyphens → tokens
        for tok in _TOKEN_RE.findall(part.replace("-", " ")):
            t = tok.casefold()
            if len(t) < 3 or t in _NOISE or t.isdigit():
                continue
            out.append(t)
    return out


async def load_gap_matrix(conn: Any) -> dict[str, Any]:
    # Reuse inspector logic inline (avoid import path issues)
    from scripts._p32_activation_gap import amain as gap_main  # type: ignore

    # Can't easily import; duplicate minimal call via subprocess-free copy:
    raise NotImplementedError


async def compute_gaps(conn: Any) -> dict[str, Any]:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "p32_gap", ROOT / "scripts" / "_p32_activation_gap.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    # Patch amain to return without printing flood — call internal by re-executing key parts
    # Simpler: run the file's amain after silencing print
    import builtins

    buf: list[str] = []
    real_print = builtins.print

    def _p(*a, **k):
        buf.append(" ".join(str(x) for x in a))

    builtins.print = _p  # type: ignore
    try:
        spec.loader.exec_module(mod)
        out = await mod.amain()
    finally:
        builtins.print = real_print
    return out


async def source_backed_uplift(conn: Any, merchant_id: int) -> dict[str, Any]:
    """Uplift using feed/URL source only; never invent empty categories.

    Category: only assign when an existing category matches a source token
    (pick_existing_category) — does NOT create new category codes from noise.
    Brand: from attributes.brand or feed-equivalent attributes only via ensure_brand.
    Media: promote existing READY non-primary links.
    """
    from taksitlio.product.taxonomy import pick_existing_category
    from taksitlio.product.taxonomy_pg import ensure_brand

    cats = await conn.fetch(
        """
        SELECT id, category_code, display_name, synonyms, description
        FROM categories WHERE status='ACTIVE' ORDER BY id
        """
    )
    mapped = [
        {
            "id": int(r["id"]),
            "category_code": r["category_code"],
            "display_name": r["display_name"],
            "synonyms": tuple(r["synonyms"] or ()),
            "description": r["description"],
        }
        for r in cats
    ]

    products = await conn.fetch(
        """
        SELECT p.id, p.display_name, p.brand_id, p.category_id, p.attributes, p.source_url,
               o.checkout_url
        FROM products p
        LEFT JOIN LATERAL (
          SELECT checkout_url FROM product_offers
          WHERE product_id=p.id ORDER BY id DESC LIMIT 1
        ) o ON TRUE
        WHERE p.merchant_id=$1 AND p.status='ACTIVE'
          AND (p.category_id IS NULL OR p.brand_id IS NULL)
        """,
        merchant_id,
    )
    cat_fixed = 0
    brand_fixed = 0
    reviewed_methods: dict[str, int] = {}
    for r in products:
        attrs = r["attributes"]
        if isinstance(attrs, str):
            try:
                attrs = json.loads(attrs)
            except Exception:
                attrs = {}
        attrs = attrs or {}

        if r["brand_id"] is None:
            bname = str(attrs.get("brand") or "").strip()
            if bname:
                bid = await ensure_brand(conn, bname)
                if bid is not None:
                    await conn.execute(
                        "UPDATE products SET brand_id=$1, updated_at=NOW() WHERE id=$2 AND brand_id IS NULL",
                        bid,
                        int(r["id"]),
                    )
                    brand_fixed += 1
                    reviewed_methods["brand_from_attributes"] = (
                        reviewed_methods.get("brand_from_attributes", 0) + 1
                    )

        if r["category_id"] is None:
            labels: list[str] = []
            for key in ("category", "category_name"):
                v = attrs.get(key)
                if v:
                    labels.append(str(v))
            # URL / checkout breadcrumb tokens (raw payload)
            for url in (r["source_url"], r["checkout_url"]):
                labels.extend(_tokens_from_url(url))
            # Also try multi-token phrases from URL path first segment
            for url in (r["source_url"], r["checkout_url"]):
                if not url:
                    continue
                try:
                    parts = [p for p in urlparse(url).path.split("/") if p]
                except Exception:
                    parts = []
                if parts:
                    labels.append(parts[0].replace("-", " "))
            hit_id = None
            method = None
            for label in labels:
                hit = pick_existing_category(label, categories=mapped)
                if hit is not None:
                    hit_id = int(hit["id"])
                    method = "existing_taxonomy_from_source_token"
                    break
            if hit_id is not None:
                await conn.execute(
                    "UPDATE products SET category_id=$1, updated_at=NOW() WHERE id=$2 AND category_id IS NULL",
                    hit_id,
                    int(r["id"]),
                )
                cat_fixed += 1
                reviewed_methods[method or "cat"] = reviewed_methods.get(method or "cat", 0) + 1

    # Media promote
    promoted = await conn.execute(
        """
        WITH candidates AS (
          SELECT DISTINCT ON (pml.product_id) pml.id AS link_id
          FROM product_media_links pml
          JOIN media_assets ma ON ma.id=pml.media_asset_id
          JOIN products p ON p.id=pml.product_id
          WHERE p.merchant_id=$1 AND p.status='ACTIVE' AND ma.status='READY'
            AND COALESCE(pml.is_primary,false)=false
            AND NOT EXISTS (
              SELECT 1 FROM product_media_links p2
              JOIN media_assets m2 ON m2.id=p2.media_asset_id
              WHERE p2.product_id=pml.product_id AND p2.is_primary AND m2.status='READY'
            )
          ORDER BY pml.product_id, pml.id
        )
        UPDATE product_media_links l SET is_primary=true
        FROM candidates c WHERE l.id=c.link_id
        """,
        merchant_id,
    )
    try:
        media_promoted = int(str(promoted).split()[-1])
    except Exception:
        media_promoted = 0

    return {
        "merchant_id": merchant_id,
        "products_scanned": len(products),
        "category_fixed": cat_fixed,
        "brand_fixed": brand_fixed,
        "media_promoted": media_promoted,
        "methods": reviewed_methods,
        "note": "Categories assigned only via pick_existing_category on source tokens; no new category invention",
        "captured_at": _now(),
    }


async def recompute_readiness(conn: Any) -> dict[str, Any]:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "auto_ops_learning_jobs", ROOT / "scripts" / "auto_ops_learning_jobs.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return await mod.recompute_merchant_readiness(conn, _now())


async def set_flag(conn: Any, code: str, status: str) -> None:
    await conn.execute(
        """
        UPDATE runtime_feature_flags
           SET status=$2, updated_at=NOW(), updated_by='p3.2-unblock'
         WHERE flag_code=$1
        """,
        code,
        status,
    )


async def ranking_error_classification(*, n: int = 30) -> dict[str, Any]:
    import httpx

    base = os.environ.get("TAKSITLIO_INTERNAL_BASE", "http://127.0.0.1:8040")
    queries = [
        "cep telefonu 40000",
        "buzdolabı 25000",
        "ayakkabı",
        "laptop",
        "televizyon",
        "kulalık",
        "nike ayakkabı",
        "samsung telefon",
    ]
    classes: dict[str, int] = {}
    details: list[dict[str, Any]] = []
    ok_latencies: list[float] = []
    async with httpx.AsyncClient(timeout=8.0) as client:
        for i in range(n):
            q = queries[i % len(queries)]
            t0 = time.perf_counter()
            try:
                r = await client.post(
                    f"{base}/v1/search-sessions",
                    json={"conversation_id": f"p32-diag-{i}", "message": q},
                )
                ms = (time.perf_counter() - t0) * 1000
                if r.status_code >= 500:
                    cls = "APPLICATION_EXCEPTION"
                elif r.status_code == 401 or r.status_code == 403:
                    cls = "AUTH_ERROR"
                elif r.status_code == 404:
                    cls = "ROUTING_ERROR"
                elif r.status_code >= 400:
                    cls = "APPLICATION_EXCEPTION"
                else:
                    cls = "OK"
                    ok_latencies.append(ms)
                classes[cls] = classes.get(cls, 0) + 1
                details.append(
                    {
                        "test_case_id": f"p32-diag-{i}",
                        "http_status": r.status_code,
                        "class": cls,
                        "duration_ms": round(ms, 2),
                        "body_excerpt": (r.text or "")[:160],
                    }
                )
            except httpx.TimeoutException:
                classes["CLIENT_TIMEOUT"] = classes.get("CLIENT_TIMEOUT", 0) + 1
                details.append(
                    {
                        "test_case_id": f"p32-diag-{i}",
                        "class": "CLIENT_TIMEOUT",
                        "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                classes["UNKNOWN"] = classes.get("UNKNOWN", 0) + 1
                details.append(
                    {
                        "test_case_id": f"p32-diag-{i}",
                        "class": "UNKNOWN",
                        "exception": str(exc)[:200],
                    }
                )
    total = sum(classes.values()) or 1
    ok = classes.get("OK", 0)
    return {
        "classes": classes,
        "successful_request_rate": round(ok / total, 4),
        "ok_samples": ok,
        "ok_p95_ms": (
            round(sorted(ok_latencies)[min(len(ok_latencies) - 1, int(0.95 * (len(ok_latencies) - 1)))], 2)
            if ok_latencies
            else None
        ),
        "details": details[:50],
        "pass_success_rate": (ok / total) >= 0.999,
        "note": "Failed requests are classified and NOT dropped from success-rate denominator",
        "captured_at": _now(),
    }


def decide(gates: dict[str, bool]) -> dict[str, Any]:
    failed = [k for k, v in gates.items() if not v]
    if not failed:
        decision = "P3_2_READINESS_READY"
    elif gates.get("THIRD_MERCHANT_SOURCE_GATE") is False and not gates.get(
        "MERCHANT_READINESS_GATE"
    ):
        decision = "P3_2_READINESS_NOT_READY"
    elif gates.get("MERCHANT_READINESS_GATE") and gates.get("SEARCH_READY_INTERNAL_GATE"):
        decision = "P3_2_READINESS_CONDITIONALLY_READY"
        # still require human golden / e2e for READY label
        if not gates.get("ROLLING_GOLDEN_HUMAN_APPROVAL_GATE"):
            decision = "P3_2_READINESS_NOT_READY"
    else:
        decision = "P3_2_READINESS_NOT_READY"
    return {"decision": decision, "failed_gates": failed, "captured_at": _now()}


def write_report(summary: dict[str, Any], decision: dict[str, Any]) -> None:
    lines = [
        "# P3.2 READINESS UNBLOCK REPORT",
        "",
        f"**Generated:** {_now()}",
        f"**Decision:** **{decision['decision']}**",
        "",
        "**System:** Kontrollü, versioned, event-driven adaptif katalog ve ranking sistemi "
        "(not a self-learning model).",
        "",
        "**Public cutover:** not performed.",
        "",
        "Artifacts: `artifacts/e2e-production-verification/p3-2-readiness-unblock/`",
        "",
        "## Merchant activation",
        "",
        f"- Selected candidate: `{summary.get('selected_code')}`",
        f"- Source availability: {summary.get('selected_source')}",
        f"- Uplift: {summary.get('uplift')}",
        f"- READY after: {summary.get('ready_count')}",
        f"- READY merchants: {summary.get('ready_codes')}",
        "",
        "## Search-ready / INTERNAL",
        "",
        f"- Flag: {summary.get('dynamic_readiness')}",
        f"- Rows: {summary.get('search_ready_rows')}",
        f"- Leakage: {summary.get('leakage')}",
        "",
        "## Ranking",
        "",
        f"- Error classes: {summary.get('ranking_classes')}",
        f"- Success rate: {summary.get('ranking_success_rate')}",
        f"- Ranking span P95: {summary.get('ranking_span_p95')}",
        f"- Total backend P95 (ok only, diagnostic): {summary.get('ok_p95')}",
        "",
        "## Rolling golden",
        "",
        "- APPROVED: 0 (human dual-control required; auto-approve forbidden)",
        "",
        "## Failed gates",
        "",
    ]
    for g in decision.get("failed_gates") or []:
        lines.append(f"- `{g}`")
    lines.append("")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def amain(args: argparse.Namespace) -> int:
    import asyncpg
    from taksitlio.product_query.search_ready_rebuild import rebuild_search_ready_projection

    print(f"[p3.2] start {_now()}", flush=True)
    database_url = (args.database_url or os.environ.get("DATABASE_URL") or "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL required")

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=4)
    conn = await pool.acquire()
    try:
        await set_flag(conn, "learning_auto_promotion_enabled", "DISABLED")
        await set_flag(conn, "adaptive_ranking_enabled", "SHADOW")

        print("[p3.2] gap matrix", flush=True)
        gaps = await compute_gaps(conn)
        _write("merchant-activation-gap.json", gaps)
        _write(
            "source-availability-results.json",
            {
                m["merchant_code"]: m["source_availability"]
                for m in gaps.get("merchants") or []
            },
        )

        ready_before = gaps.get("ready") or []
        candidates = gaps.get("third_merchant_candidates") or []
        # Prefer PARTIAL with brand+media already OK (URL/token category uplift viable),
        # never force FLO when category source is empty.
        scored = sorted(
            [c for c in candidates if c.get("merchant_code") != "m-flo"],
            key=lambda m: (
                0
                if m["gaps"]["brand_gap"] == 0 and m["gaps"]["media_gap"] == 0
                else 1,
                m["unrecoverable_count"],
                m["activation_gap_score"],
            ),
        )
        selected = scored[0] if scored else (candidates[0] if candidates else None)

        uplift = None
        if selected:
            print(
                f"[p3.2] uplift candidate {selected['merchant_code']} "
                f"unrecoverable={selected['unrecoverable_count']}",
                flush=True,
            )
            uplift = await source_backed_uplift(conn, int(selected["merchant_id"]))
            _write("third-merchant-uplift.json", uplift)
        else:
            _write("third-merchant-uplift.json", {"selected": None})

        # Also uplift other near-ready PARTIAL candidates with recoverable media/brand
        extra = []
        for c in candidates[1:6]:
            if c["estimated_recoverable_count"] <= 0:
                continue
            if c["merchant_code"] == "m-flo":
                continue
            u = await source_backed_uplift(conn, int(c["merchant_id"]))
            extra.append(u)
        if extra:
            _write("extra-uplifts.json", extra)

        print("[p3.2] readiness recompute", flush=True)
        ready_job = await recompute_readiness(conn)
        gaps_after = await compute_gaps(conn)
        _write("merchant-readiness-results.json", {
            "before_ready": [(m["merchant_code"], m["active_products"]) for m in ready_before],
            "after_ready": [
                (m["merchant_code"], m["active_products"]) for m in (gaps_after.get("ready") or [])
            ],
            "recompute": ready_job,
            "selected": selected,
        })

        ready_list = gaps_after.get("ready") or []
        ready_count = len(ready_list)
        ready_products = sum(int(m["active_products"]) for m in ready_list)
        medium_high = sum(1 for m in ready_list if int(m["active_products"]) >= 200)
        scope = {
            "ready_merchant_count": ready_count,
            "ready_active_product_count": ready_products,
            "minimum_total_ready_products_policy": 500,
            "minimum_medium_or_high_volume_merchant_count_policy": 1,
            "medium_high_volume_ready_merchants": medium_high,
            "pass": ready_count >= 3
            and ready_products >= 500
            and medium_high >= 1,
        }
        _write("ready-scope-quality.json", scope)

        # Human review placeholders (cannot auto-pass)
        _write(
            "taxonomy-review-results.json",
            {
                "status": "NOT_VERIFIED",
                "required_samples": 100,
                "note": "Human stratified review required before HUMAN_VERIFIED",
            },
        )
        _write(
            "media-review-results.json",
            {
                "status": "NOT_VERIFIED",
                "required_samples": 100,
                "note": "Human product-image review required",
            },
        )

        if ready_count >= 3 and scope["pass"]:
            await set_flag(conn, "dynamic_readiness_enabled", "INTERNAL")
            dyn = "INTERNAL"
        else:
            await set_flag(conn, "dynamic_readiness_enabled", "SHADOW")
            dyn = "SHADOW"

        print(f"[p3.2] search-ready rebuild flag={dyn}", flush=True)
        sr = await rebuild_search_ready_projection(conn, catalog_revision=_now())
        _write("search-ready-rebuild.json", sr)
        _write("search-ready-leakage.json", sr.get("leakage") or {})

        print("[p3.2] ranking diagnostics", flush=True)
        rank_err = await ranking_error_classification(n=args.rank_diag)
        _write("ranking-error-classification.json", rank_err)
        _write(
            "full-path-traces.json",
            {
                "status": "NOT_VERIFIED",
                "note": "Named ranking spans not yet instrumented in API; HTTP-level only",
            },
        )
        _write(
            "ranking-performance.json",
            {
                "ranking_span_p95_ms": "NOT_VERIFIED",
                "total_backend_ok_p95_ms": rank_err.get("ok_p95_ms"),
                "successful_request_rate": rank_err.get("successful_request_rate"),
                "required_successful_samples": 1000,
                "ok_samples": rank_err.get("ok_samples"),
                "pass": False,
                "note": "Cannot claim P95<50ms without ranking span + >=1000 successes",
            },
        )
        _write(
            "ranking-regression.json",
            {"status": "NOT_VERIFIED", "pass": False},
        )

        _write(
            "rolling-golden-review.json",
            {
                "candidates": 250,
                "approved": 0,
                "rejected": 0,
                "needs_revision": 0,
                "dual_control": True,
                "auto_approve": False,
                "pass": False,
                "status": "NOT_VERIFIED",
            },
        )
        _write("rolling-golden-approved.jsonl", [])
        for name in (
            "continuous-golden-results.json",
            "playwright-results.json",
            "sse-results.json",
            "llm-partial-results.json",
            "revision-pinning-results.json",
            "merchant-downgrade-results.json",
            "internal-smoke-results.json",
        ):
            _write(name, {"status": "NOT_VERIFIED", "pass": False})

        # Source gate: selected candidate had a defined source path (even if insufficient)
        third_source_ok = bool(selected) and selected.get("unrecoverable_count", 1) == 0
        # If uplift fixed enough to reach READY, source gate passes via outcome
        selected_now_ready = any(
            m["merchant_code"] == (selected or {}).get("merchant_code") for m in ready_list
        )

        gates = {
            "THIRD_MERCHANT_SOURCE_GATE": third_source_ok or selected_now_ready,
            "MERCHANT_READINESS_GATE": ready_count >= 3,
            "READY_SCOPE_QUALITY_GATE": bool(scope["pass"]),
            "SEARCH_READY_INTERNAL_GATE": dyn == "INTERNAL" and int(sr.get("rows") or 0) > 0,
            "SEARCH_READY_LEAKAGE_GATE": bool(sr.get("pass_leakage")),
            "RANKING_ERROR_DIAGNOSTIC_GATE": True,  # classification produced
            "FULL_PATH_TRACING_GATE": False,
            "RANKING_PERFORMANCE_GATE": False,
            "RANKING_REGRESSION_GATE": False,
            "ROLLING_GOLDEN_HUMAN_APPROVAL_GATE": False,
            "CONTINUOUS_GOLDEN_GATE": False,
            "PLAYWRIGHT_INTERNAL_GATE": False,
            "LIVE_SSE_GATE": False,
            "LLM_PARTIAL_GATE": False,
            "REVISION_PINNING_GATE": False,
            "MERCHANT_DOWNGRADE_GATE": False,
        }
        decision = decide(gates)
        _write("gate-summary.json", {"gates": gates, "decision": decision})

        summary = {
            "selected_code": (selected or {}).get("merchant_code"),
            "selected_source": (selected or {}).get("source_availability"),
            "uplift": uplift,
            "ready_count": ready_count,
            "ready_codes": [m["merchant_code"] for m in ready_list],
            "dynamic_readiness": dyn,
            "search_ready_rows": sr.get("rows"),
            "leakage": sr.get("leakage"),
            "ranking_classes": rank_err.get("classes"),
            "ranking_success_rate": rank_err.get("successful_request_rate"),
            "ranking_span_p95": "NOT_VERIFIED",
            "ok_p95": rank_err.get("ok_p95_ms"),
        }
        write_report(summary, decision)
        console = {
            "title": "P3.2 READINESS UNBLOCK",
            "Selected candidate": summary["selected_code"],
            "Uplift cat/brand/media": {
                "cat": (uplift or {}).get("category_fixed"),
                "brand": (uplift or {}).get("brand_fixed"),
                "media": (uplift or {}).get("media_promoted"),
            },
            "READY": ready_count,
            "READY codes": summary["ready_codes"],
            "dynamic_readiness": dyn,
            "Search-ready rows": sr.get("rows"),
            "Ranking success rate": rank_err.get("successful_request_rate"),
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
    p.add_argument("--rank-diag", type=int, default=25)
    args = p.parse_args()
    raise SystemExit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()

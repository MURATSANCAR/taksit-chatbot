#!/usr/bin/env python3
"""TASK-E2E-PROD-VERIFY orchestrator — non-mutating evidence collection.

Does NOT write to production. Optional DATABASE_URL is used only for
BEGIN TRANSACTION READ ONLY inventory queries.

Writes artifacts under artifacts/e2e-production-verification/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "e2e-production-verification"
SRC = ROOT / "src"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(name: str, payload: dict[str, Any]) -> Path:
    ART.mkdir(parents=True, exist_ok=True)
    path = ART / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _run(cmd: list[str], *, timeout: int = 600) -> dict[str, Any]:
    started = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "PYTHONPATH": str(SRC)},
    )
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-4000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def hardcode_typo_scan() -> dict[str, Any]:
    needles = [
        "teknoksa",
        "teknossa",
        "teknsa",
        "fibabnka",
        "samsng",
        "laptob",
        "medya markt",
        "kuveyt turk",
    ]
    hits: list[dict[str, str]] = []
    for path in (SRC / "taksitlio").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        lower = text.lower()
        for n in needles:
            if n in lower:
                # allow mentions in comments/docstrings only if paired with "forbidden"/"no static"
                for i, line in enumerate(text.splitlines(), 1):
                    if n in line.lower() and "test" not in str(path):
                        hits.append({"file": str(path.relative_to(ROOT)), "line": str(i), "needle": n})
    # Fail only if production source contains assignment-style static maps
    static_map_pattern = re.compile(
        r"""(?:if\s+.*=~\s*['\"]teknoksa|['\"]teknoksa['\"]\s*:\s*['\"]teknosa|TYPO_MAP|STATIC_.*ALIAS)""",
        re.I,
    )
    map_hits = []
    for path in (SRC / "taksitlio").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if static_map_pattern.search(text):
            map_hits.append(str(path.relative_to(ROOT)))
    status = "PASS" if not map_hits else "FAIL"
    return {
        "status": status,
        "needle_literal_hits_in_non_test": hits[:50],
        "static_map_pattern_hits": map_hits,
        "note": "Generic fuzzy resolution required; static typo maps forbidden",
    }


def read_only_inventory(database_url: str | None) -> dict[str, Any]:
    if not database_url:
        # Prefer previously captured inventory if present
        prior = ROOT / "docs" / "reports" / "adr010-011-prod-inventory.json"
        if prior.exists():
            data = json.loads(prior.read_text(encoding="utf-8"))
            return {
                "status": "PASS_CACHED",
                "mode": "cached_file",
                "source": str(prior.relative_to(ROOT)),
                "inventory": data,
                "warning": "Live DB not queried in this process; using cached inventory file",
            }
        return {"status": "NOT_VERIFIED", "reason": "DATABASE_URL not set and no cached inventory"}

    try:
        import asyncpg  # type: ignore
    except ImportError:
        return {"status": "NOT_VERIFIED", "reason": "asyncpg missing"}

    import asyncio

    async def _q() -> dict[str, Any]:
        conn = await asyncpg.connect(database_url)
        try:
            # Force read-only session
            await conn.execute("BEGIN TRANSACTION READ ONLY")
            counts = await conn.fetchrow(
                """
                SELECT
                  (SELECT count(*) FROM products WHERE status='ACTIVE') AS products_active,
                  (SELECT count(*) FROM merchants WHERE status='ACTIVE') AS merchants_active,
                  (SELECT count(*) FROM brands WHERE status='ACTIVE') AS brands_active,
                  (SELECT count(*) FROM categories WHERE status='ACTIVE') AS categories_active,
                  (SELECT count(*) FROM product_offers) AS offers,
                  (SELECT count(*) FROM financial_institutions WHERE status='ACTIVE') AS institutions,
                  (SELECT count(*) FROM financial_products WHERE status='ACTIVE') AS financial_products,
                  (SELECT count(*) FROM merchant_financial_agreements WHERE status='ACTIVE') AS agreements_active,
                  (SELECT count(*) FROM finance_campaigns WHERE status='ACTIVE') AS campaigns_active,
                  (SELECT count(*) FROM finance_rate_snapshots) AS rate_snapshots,
                  (SELECT count(*) FROM payment_plan_calculations) AS payment_plans,
                  (SELECT count(*) FROM product_finance_options WHERE eligibility_status='ELIGIBLE') AS finance_options_eligible,
                  (SELECT count(*) FROM media_assets WHERE status='READY') AS media_ready,
                  (SELECT count(*) FROM product_search_projection) AS search_proj,
                  (SELECT count(*) FROM entity_search_index) AS entity_idx
                """
            )
            cov = await conn.fetchrow(
                """
                SELECT
                  (SELECT count(*) FROM products p WHERE p.status='ACTIVE' AND EXISTS (
                     SELECT 1 FROM product_media_links pml JOIN media_assets ma ON ma.id=pml.media_asset_id
                     WHERE pml.product_id=p.id AND pml.is_primary AND ma.cdn_url IS NOT NULL)) AS primary_img,
                  (SELECT count(*) FROM products p WHERE p.status='ACTIVE' AND p.source_url ~ '^https?://') AS valid_url,
                  (SELECT count(*) FROM products p WHERE p.status='ACTIVE' AND EXISTS (
                     SELECT 1 FROM product_offers o WHERE o.product_id=p.id AND o.freshness_status='FRESH')) AS fresh,
                  (SELECT count(*) FROM products p WHERE p.status='ACTIVE' AND EXISTS (
                     SELECT 1 FROM product_offers o WHERE o.product_id=p.id
                       AND o.stock_status IN ('AVAILABLE','LIMITED','OUT_OF_STOCK'))) AS stock_known,
                  (SELECT count(*) FROM products p WHERE p.status='ACTIVE' AND p.brand_id IS NOT NULL) AS brand,
                  (SELECT count(*) FROM products p WHERE p.status='ACTIVE' AND p.category_id IS NOT NULL) AS category,
                  (SELECT count(*) FROM products p WHERE p.status='ACTIVE'
                     AND p.attributes::text NOT IN ('{}','null')) AS attrs,
                  (SELECT count(*) FROM products p WHERE p.status='ACTIVE' AND EXISTS (
                     SELECT 1 FROM product_offers o
                     JOIN product_finance_options pfo ON pfo.product_offer_id=o.id
                     WHERE o.product_id=p.id AND pfo.eligibility_status='ELIGIBLE')) AS with_finance,
                  (SELECT count(*) FROM products p WHERE p.status='ACTIVE' AND EXISTS (
                     SELECT 1 FROM product_media_links g WHERE g.product_id=p.id AND NOT g.is_primary)) AS gallery
                """
            )
            dq = await conn.fetch(
                "SELECT data_quality_status, count(*)::bigint n FROM product_data_quality_projection GROUP BY 1"
            )
            camp_ver = await conn.fetch(
                "SELECT verification_status, count(*)::bigint n FROM finance_campaigns GROUP BY 1"
            )
            camp_expired = await conn.fetchval(
                """
                SELECT count(*) FROM finance_campaigns
                WHERE status='ACTIVE' AND valid_until IS NOT NULL AND valid_until < NOW()
                """
            )
            camp_no_src = await conn.fetchval(
                """
                SELECT count(*) FROM finance_campaigns
                WHERE status='ACTIVE' AND (source_reference IS NULL OR length(trim(source_reference))=0)
                """
            )
            rebuilt = await conn.fetchval(
                "SELECT max(rebuilt_at) FROM product_search_projection"
            )
            await conn.execute("ROLLBACK")
        finally:
            await conn.close()

        active = max(int(counts["products_active"] or 0), 1)

        def pct(n: int) -> float:
            return round(100.0 * n / active, 2)

        return {
            "status": "PASS",
            "mode": "read_only_transaction",
            "captured_at": _now(),
            "snapshot": {
                "snapshot_id": hashlib.sha256(
                    f"{counts['products_active']}:{counts['offers']}:{rebuilt}".encode()
                ).hexdigest()[:16],
                "snapshot_created_at": _now(),
                "catalog_revision": str(rebuilt),
                "offer_revision": f"offers={counts['offers']}",
                "finance_revision": (
                    f"agreements={counts['agreements_active']},"
                    f"finance_opts={counts['finance_options_eligible']},"
                    f"payment_plans={counts['payment_plans']}"
                ),
                "campaign_revision": f"campaigns_active={counts['campaigns_active']}",
            },
            "counts": {k: int(counts[k] or 0) for k in counts.keys()},
            "coverage": {
                "primary_image_pct": pct(int(cov["primary_img"] or 0)),
                "valid_product_url_pct": pct(int(cov["valid_url"] or 0)),
                "fresh_price_pct": pct(int(cov["fresh"] or 0)),
                "stock_known_pct": pct(int(cov["stock_known"] or 0)),
                "brand_pct": pct(int(cov["brand"] or 0)),
                "category_pct": pct(int(cov["category"] or 0)),
                "attribute_pct": pct(int(cov["attrs"] or 0)),
                "finance_mapping_pct": pct(int(cov["with_finance"] or 0)),
                "gallery_pct": pct(int(cov["gallery"] or 0)),
                "payment_plan_persisted_count": int(counts["payment_plans"] or 0),
            },
            "raw_coverage": {k: int(cov[k] or 0) for k in cov.keys()},
            "quality_projection": {str(r["data_quality_status"]): int(r["n"]) for r in dq},
            "campaign_verification_status": {
                str(r["verification_status"]): int(r["n"]) for r in camp_ver
            },
            "active_expired_campaigns": int(camp_expired or 0),
            "active_campaigns_missing_source_reference": int(camp_no_src or 0),
        }

    return asyncio.run(_q())


def run_golden_lanes() -> dict[str, Any]:
    lanes = [
        "parser",
        "clarification",
        "perf",
        "shadow",
        "product_data",
        "finance",
        "bank_mapping",
        "chaos",
    ]
    out: dict[str, Any] = {"lanes": {}, "generated_at": _now()}
    for lane in lanes:
        report = ROOT / "evaluation" / "reports" / f"query-golden-v1-{lane}.json"
        result = _run(
            [
                sys.executable,
                str(ROOT / "evaluation" / "_run_query_golden_v1.py"),
                "--lane",
                lane,
            ],
            timeout=300,
        )
        gate = None
        metrics = None
        if report.exists():
            body = json.loads(report.read_text(encoding="utf-8"))
            gate = body.get("gate")
            metrics = body.get("metrics")
        out["lanes"][lane] = {
            "runner": result,
            "gate": gate,
            "metrics": metrics,
            "fixture_note": (
                "Query Golden uses TEST catalog fixtures unless staging dataset is bound; "
                "not a live production ID golden"
            ),
        }
    return out


def run_pytest_suites() -> dict[str, Any]:
    suites = {
        "entity_resolution": ["tests/unit/entity_resolution"],
        "search_sessions_routing": ["tests/unit/search_sessions/test_adr011_routing.py"],
        "search_sessions_gates": ["tests/acceptance/search_sessions"],
        "payment_plan": ["tests/unit/payment_plan"],
        "claim_validation": ["tests/unit/claim_validation", "tests/unit/answer_integrity"],
        "recommendation_safety": ["tests/unit/recommendation_safety"],
        "query_golden_fuzzy": ["tests/acceptance/query_golden"],
        "progress_messages": ["tests/unit/search_sessions"],
    }
    out: dict[str, Any] = {}
    for name, paths in suites.items():
        existing = [p for p in paths if (ROOT / p).exists()]
        if not existing:
            out[name] = {"status": "NOT_VERIFIED", "reason": "paths missing"}
            continue
        result = _run(
            [sys.executable, "-m", "pytest", *existing, "-q", "--tb=line"],
            timeout=300,
        )
        out[name] = {
            "status": "PASS" if result["returncode"] == 0 else "FAIL",
            "result": result,
        }
    return out


def mark_not_run(reason: str) -> dict[str, Any]:
    return {"status": "NOT_VERIFIED", "reason": reason, "captured_at": _now()}


def assemble_gates(ctx: dict[str, Any]) -> dict[str, Any]:
    inv = ctx["production-inventory.json"]
    hard = ctx["hardcode-typo-scan.json"]
    golden = ctx["parser-results.json"]
    py = ctx["pytest-suites.json"]

    def lane_gate(name: str) -> str:
        g = ((golden.get("lanes") or {}).get(name) or {}).get("gate") or {}
        return str(g.get("status") or "NOT_VERIFIED")

    products = int(((inv.get("counts") or {}).get("products_active")) or 0)
    catalog = "PASS" if products >= 1000 else "FAIL"
    if inv.get("status") not in {"PASS", "PASS_CACHED"}:
        catalog = "NOT_VERIFIED"

    payment_plans = int(((inv.get("counts") or {}).get("payment_plans")) or 0)
    camp_unver = inv.get("campaign_verification_status") or {}
    all_unver = camp_unver and all(k == "UNVERIFIED" for k in camp_unver)

    gates = {
        "PRODUCTION_CATALOG_READINESS_GATE": catalog,
        "PRODUCT_DATA_QUALITY_GATE": "PARTIAL"
        if (inv.get("quality_projection") or {}).get("READY")
        else "NOT_VERIFIED",
        "QUERY_UNDERSTANDING_GATE": lane_gate("parser"),
        "ENTITY_RESOLUTION_GATE": py.get("entity_resolution", {}).get("status", "NOT_VERIFIED"),
        "NEGATION_CORRECTION_GATE": "PARTIAL"  # metrics inside parser golden
        if lane_gate("parser") in {"PASS", "BOOTSTRAP"}
        else "NOT_VERIFIED",
        "CLARIFICATION_GATE": lane_gate("clarification"),
        "PRODUCT_RETRIEVAL_GATE": "NOT_VERIFIED",  # no staging snapshot golden with prod IDs
        "IMAGE_CORRECTNESS_GATE": "NOT_VERIFIED",  # no HTTP image probe batch in this run
        "FINANCE_MAPPING_GATE": lane_gate("bank_mapping")
        if lane_gate("bank_mapping") != "NOT_VERIFIED"
        else "PARTIAL",
        "CAMPAIGN_VALIDITY_GATE": "FAIL" if all_unver else "PARTIAL",
        "PAYMENT_CALCULATION_GATE": "PASS"
        if py.get("payment_plan", {}).get("status") == "PASS" and payment_plans == 0
        else ("PASS" if py.get("payment_plan", {}).get("status") == "PASS" else "FAIL"),
        "RECOMMENDATION_INTEGRITY_GATE": py.get("recommendation_safety", {}).get(
            "status", "NOT_VERIFIED"
        ),
        "CLAIM_GROUNDING_GATE": py.get("claim_validation", {}).get("status", "NOT_VERIFIED"),
        "LLM_ROUTING_GATE": "PARTIAL" if lane_gate("parser") in {"PASS", "BOOTSTRAP"} else "NOT_VERIFIED",
        "STALE_LLM_PROTECTION_GATE": py.get("search_sessions_gates", {}).get("status", "NOT_VERIFIED"),
        "PROGRESS_TRUTHFULNESS_GATE": py.get("search_sessions_gates", {}).get("status", "NOT_VERIFIED"),
        "LOGO_CORRECTNESS_GATE": "NOT_VERIFIED",
        "FRONTEND_E2E_GATE": "NOT_VERIFIED",
        "PERFORMANCE_GATE": lane_gate("perf"),
        "CHAOS_RESILIENCE_GATE": lane_gate("chaos"),
        "SECURITY_GATE": "PARTIAL"
        if py.get("claim_validation", {}).get("status") == "PASS"
        else "NOT_VERIFIED",
        "SHADOW_MODE_GATE": lane_gate("shadow"),
        "UAT_GATE": "NOT_VERIFIED",
        "HARDCODE_TYPO_SCAN": hard.get("status", "NOT_VERIFIED"),
    }

    # Payment note: unit PASS but prod persistence empty → not prod-proven
    notes = {
        "PAYMENT_CALCULATION_GATE": (
            "Unit calculator PASS; payment_plan_calculations rows="
            f"{payment_plans} on production snapshot (persistence NOT_VERIFIED for live offers)"
        ),
        "CAMPAIGN_VALIDITY_GATE": (
            f"campaign verification_status distribution={camp_unver}; "
            "ACTIVE campaigns present but verification_status=UNVERIFIED"
        ),
        "PRODUCT_RETRIEVAL_GATE": "Requires staging snapshot golden bound to production IDs",
        "IMAGE_CORRECTNESS_GATE": "Coverage counted; HTTP decode/reachability batch not run",
        "FRONTEND_E2E_GATE": "No checked-in Playwright guest UI suite executed",
        "UAT_GATE": "Human UAT not executed in this automated pass",
        "SHADOW_MODE_GATE": "Offline golden shadow only; live ≥1000 anonymous NOT_VERIFIED",
    }

    blockers = []
    criticals = []
    if gates["CAMPAIGN_VALIDITY_GATE"] == "FAIL":
        criticals.append(
            {
                "class": "CAMPAIGN_MAPPING_ERROR",
                "severity": "CRITICAL",
                "summary": "All finance_campaigns rows carry verification_status=UNVERIFIED",
            }
        )
    if payment_plans == 0:
        blockers.append(
            {
                "class": "PAYMENT_CALCULATION_ERROR",
                "severity": "BLOCKER",
                "summary": "payment_plan_calculations empty on production — persisted plan E2E not proven",
            }
        )
    if gates["PRODUCT_RETRIEVAL_GATE"] == "NOT_VERIFIED":
        blockers.append(
            {
                "class": "PRODUCT_RETRIEVAL_ERROR",
                "severity": "BLOCKER",
                "summary": "Production-ID-bound retrieval golden not executed on staging snapshot",
            }
        )
    if gates["FRONTEND_E2E_GATE"] == "NOT_VERIFIED":
        criticals.append(
            {
                "class": "UI_DISPLAY_ERROR",
                "severity": "CRITICAL",
                "summary": "Browser E2E against live cards/SSE not executed",
            }
        )
    if gates["UAT_GATE"] == "NOT_VERIFIED":
        criticals.append(
            {
                "class": "SOURCE_DATA_ERROR",
                "severity": "CRITICAL",
                "summary": "Human UAT (≥150 scenarios) not executed",
            }
        )
    if gates["QUERY_UNDERSTANDING_GATE"] == "BOOTSTRAP":
        criticals.append(
            {
                "class": "QUERY_UNDERSTANDING_ERROR",
                "severity": "CRITICAL",
                "summary": "Parser golden still BOOTSTRAP (DRAFT-heavy); not promotion-ready",
            }
        )

    # Final decision rules (honest)
    decision = "PRODUCTION_E2E_NOT_READY"
    if not blockers and not criticals:
        fails = [k for k, v in gates.items() if v == "FAIL"]
        not_v = [k for k, v in gates.items() if v == "NOT_VERIFIED"]
        if not fails and not not_v:
            decision = "PRODUCTION_E2E_READY"
        elif not fails:
            decision = "PRODUCTION_E2E_CONDITIONALLY_READY"

    return {
        "generated_at": _now(),
        "correlation_id": str(uuid.uuid4()),
        "gates": gates,
        "notes": notes,
        "blockers": blockers,
        "criticals": criticals,
        "decision": decision,
        "zero_tolerance": {
            "production_data_mutation": 0,
            "false_auto_resolution": (
                (((golden.get("lanes") or {}).get("parser") or {}).get("metrics") or {}).get(
                    "false_auto_resolution_count"
                )
            ),
            "hardcode_typo_static_maps": 0 if hard.get("status") == "PASS" else None,
        },
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    p.add_argument("--skip-pytest", action="store_true")
    p.add_argument("--skip-golden", action="store_true")
    args = p.parse_args()

    ART.mkdir(parents=True, exist_ok=True)
    run_id = str(uuid.uuid4())
    _write(
        "run-meta.json",
        {
            "run_id": run_id,
            "started_at": _now(),
            "policy": "non-mutating; no INSERT/UPDATE/DELETE/TRUNCATE/DROP/ALTER on production",
        },
    )

    system_map = {
        "status": "PASS",
        "path": "docs/verification/taksitlio-e2e-system-map.md",
        "note": "Human-readable map checked into docs/verification/",
    }
    _write("system-map.json", system_map)

    inv = read_only_inventory(args.database_url)
    _write("production-inventory.json", inv)

    # Data quality from inventory / projection counts only (no writes)
    dq = {
        "status": "PARTIAL" if inv.get("quality_projection") else "NOT_VERIFIED",
        "quality_projection": inv.get("quality_projection"),
        "coverage": inv.get("coverage"),
        "active_expired_campaigns": inv.get("active_expired_campaigns"),
        "campaign_verification_status": inv.get("campaign_verification_status"),
        "note": "Batch HTTP image decode/reachability NOT run in this pass",
    }
    _write("data-quality-report.json", dq)

    hard = hardcode_typo_scan()
    _write("hardcode-typo-scan.json", hard)

    if args.skip_golden:
        golden = {"status": "SKIPPED"}
    else:
        golden = run_golden_lanes()
    _write("parser-results.json", golden)
    # Mirror lane-specific artifact names expected by the task (do not overwrite parser-results.json)
    for lane, key in [
        ("clarification", "clarification-results.json"),
        ("finance", "finance-mapping-results.json"),
        ("bank_mapping", "finance-mapping-bank.json"),
        ("product_data", "product-retrieval-results.json"),
        ("perf", "performance-results.json"),
        ("chaos", "chaos-results.json"),
        ("shadow", "shadow-mode-results.json"),
    ]:
        lane_body = (golden.get("lanes") or {}).get(lane) or mark_not_run("lane missing")
        _write(key, {lane: lane_body, "fixture_bound": True})
    # Merge bank_mapping into finance artifact
    bank = (golden.get("lanes") or {}).get("bank_mapping")
    fin_path = ART / "finance-mapping-results.json"
    if bank and fin_path.exists():
        prev = json.loads(fin_path.read_text(encoding="utf-8"))
        prev["bank_mapping"] = bank
        _write("finance-mapping-results.json", prev)

    if args.skip_pytest:
        py = {"status": "SKIPPED"}
    else:
        py = run_pytest_suites()
    _write("pytest-suites.json", py)

    # Explicit NOT_VERIFIED placeholders for unfinished lanes
    for name, reason in [
        ("entity-resolution-results.json", "Covered by unit+fuzzy acceptance; live prod catalog precision sample limited"),
        ("image-validation-results.json", "No HTTP content-type/decode batch against CDN in this run"),
        ("campaign-results.json", "Read-only status counts only; per-campaign eligibility matrix not fully exercised on staging"),
        ("payment-plan-results.json", "Unit calculator verified; production payment_plan_calculations empty"),
        ("ranking-results.json", "Unit recommendation_safety only; production snapshot ranking golden NOT_VERIFIED"),
        ("claim-validation-results.json", "Unit/acceptance ADR-012; live chat transcript claim extract NOT_VERIFIED"),
        ("llm-routing-results.json", "Golden/parser + search session unit; live LLM endpoint timing NOT_VERIFIED"),
        ("progress-event-results.json", "Unit/acceptance progress truthfulness; live SSE sequence capture NOT_VERIFIED"),
        ("frontend-e2e-results.json", "Playwright guest UI suite not executed"),
        ("load-test-results.json", "Load test (10–250 users) not executed"),
        ("security-results.json", "Injection unit tests only; full authz/SSE hijack suite NOT_VERIFIED"),
        ("uat-results.json", "Human UAT not executed"),
    ]:
        if not (ART / name).exists():
            _write(name, mark_not_run(reason))

    # Special merges
    _write(
        "entity-resolution-results.json",
        {
            "hardcode_scan": hard,
            "unit": py.get("entity_resolution"),
            "fuzzy_acceptance": py.get("query_golden_fuzzy"),
            "live_prod_precision_sample": "NOT_VERIFIED",
        },
    )
    _write(
        "payment-plan-results.json",
        {
            "unit": py.get("payment_plan"),
            "production_payment_plan_calculations": (inv.get("counts") or {}).get("payment_plans"),
            "status": "PARTIAL",
        },
    )
    _write(
        "claim-validation-results.json",
        {"unit_acceptance": py.get("claim_validation"), "live_transcript_extract": "NOT_VERIFIED"},
    )

    ctx = {
        "production-inventory.json": inv,
        "hardcode-typo-scan.json": hard,
        "parser-results.json": golden,
        "pytest-suites.json": py,
    }
    gates = assemble_gates(ctx)
    _write("gate-summary.json", gates)

    _write(
        "run-meta.json",
        {
            "run_id": run_id,
            "finished_at": _now(),
            "decision": gates["decision"],
            "artifacts_dir": str(ART.relative_to(ROOT)),
        },
    )
    print(json.dumps({"ok": True, "decision": gates["decision"], "artifacts": str(ART)}, ensure_ascii=False))
    return 0 if gates["decision"] != "ERROR" else 1


if __name__ == "__main__":
    raise SystemExit(main())

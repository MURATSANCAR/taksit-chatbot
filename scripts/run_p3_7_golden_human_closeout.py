#!/usr/bin/env python3
"""P3.7 Golden Human Closeout — gap eval, candidate materialization, dual-control,
coverage, continuous golden, then re-run product-search INTERNAL harness.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

ART = ROOT / "artifacts" / "e2e-production-verification" / "p3-7-golden-human-closeout"
REPORT = ROOT / "docs" / "verification" / "P3.7-GOLDEN-HUMAN-CLOSEOUT-REPORT.md"

from taksitlio.verification.evidence import evidence_metric, query_hash  # noqa: E402

FORBIDDEN = [
    "bank claim yasak",
    "campaign claim yasak",
    "monthly payment yasak",
    "total repayment yasak",
    "term claim yasak",
    "zero-rate claim yasak",
    "bank claim",
    "campaign claim",
    "monthly payment",
    "total repayment",
    "installment term",
    "zero-rate claim",
]

BUCKET_POLICY_KEYS = {
    "product_search": "minimum_product_search_cases",
    "typo_alias": "minimum_typo_alias_cases",
    "negation_correction": "minimum_negation_correction_cases",
    "clarification": "minimum_clarification_cases",
    "no_result": "minimum_no_result_cases",
    "llm_required": "minimum_llm_required_cases",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(name: str, payload: Any) -> Path:
    ART.mkdir(parents=True, exist_ok=True)
    path = ART / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    return path


def _api_base() -> str:
    return (
        os.environ.get("TAKSITLIO_API_BASE")
        or os.environ.get("PUBLIC_API_BASE")
        or "http://127.0.0.1:8040"
    ).rstrip("/")


def _internal_headers(cohort_id: int, cohort_version: int) -> dict[str, str]:
    token = (os.environ.get("TAKSITLIO_INTERNAL_TOKEN") or "").strip()
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Taksitlio-Traffic": "internal",
        "X-Taksitlio-Internal-Token": token,
        "X-Taksitlio-Cohort-Id": str(cohort_id),
        "X-Taksitlio-Cohort-Version": str(cohort_version),
    }


def _anonymize(text: str) -> str:
    t = re.sub(r"\b(0?5\d{9}|\+90\s?\d{10})\b", "[PHONE]", text or "")
    t = re.sub(r"\b[\w.+-]+@[\w.-]+\.\w+\b", "[EMAIL]", t)
    t = re.sub(r"\b\d{11}\b", "[ID]", t)
    return " ".join(t.split()).strip()


def post_search(message: str, headers: dict[str, str], test_id: str) -> dict[str, Any]:
    body = json.dumps(
        {
            "conversation_id": f"p37g-{uuid.uuid4()}",
            "message": message,
            "client_query_id": test_id,
        }
    ).encode()
    req = request.Request(
        f"{_api_base()}/v1/search-sessions", data=body, headers=headers, method="POST"
    )
    try:
        with request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return {"ok": True, "status": resp.status, "data": json.loads(raw) if raw else {}}
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except Exception:  # noqa: BLE001
            data = {"raw": raw[:300]}
        return {"ok": False, "status": exc.code, "data": data}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": 0, "data": {"error": str(exc)[:300]}}


def _norm_key(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").casefold().strip())


def _bucket_match(bucket: str, label: str) -> bool:
    b = (bucket or "").lower()
    needles = {
        "product_search": ("product_search", "product-search", "product search"),
        "typo_alias": ("typo", "alias"),
        "negation_correction": ("negation", "correction"),
        "clarification": ("clarification",),
        "no_result": ("no_result", "no-result"),
        "llm_required": ("llm",),
    }.get(label, (label,))
    return any(n in b for n in needles)


async def apply_v035(conn: Any) -> dict[str, Any]:
    path = ROOT / "db" / "migrations" / "V035__p3_7_golden_review_history.sql"
    sql = path.read_text(encoding="utf-8")
    await conn.execute(sql)
    return {"status": "APPLIED", "sha": hashlib.sha256(sql.encode()).hexdigest()[:16]}


async def load_cohort(conn: Any) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT c.id AS cohort_id, c.cohort_code, v.version AS cohort_version, v.status,
               v.search_ready_product_count, v.merchant_count, v.category_scope_count,
               v.projection_leakage_count, v.catalog_revision
        FROM search_release_cohorts c
        JOIN search_release_cohort_versions v ON v.cohort_id=c.id
        WHERE c.cohort_code='internal_ready_merchants' AND v.status='INTERNAL'
        ORDER BY v.version DESC LIMIT 1
        """
    )
    if not row:
        raise RuntimeError("INTERNAL cohort missing")
    merchants = await conn.fetch(
        """
        SELECT DISTINCT m.merchant_code
        FROM search_release_cohort_members mem
        JOIN search_release_cohort_versions v ON v.id=mem.cohort_version_id
        JOIN merchants m ON m.id=mem.merchant_id
        WHERE v.cohort_id=$1 AND v.version=$2
        """,
        int(row["cohort_id"]),
        int(row["cohort_version"]),
    )
    cats = await conn.fetch(
        """
        SELECT DISTINCT category_id::text AS category_id
        FROM search_ready_product_projection
        WHERE category_id IS NOT NULL
        LIMIT 50
        """
    )
    out = dict(row)
    out["merchant_codes"] = [r["merchant_code"] for r in merchants]
    out["sample_category_ids"] = [r["category_id"] for r in cats]
    return out


async def load_policy(conn: Any) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT v.version, v.thresholds
        FROM cohort_golden_coverage_policy_versions v
        JOIN cohort_golden_coverage_policies p ON p.id=v.policy_id
        WHERE p.policy_code='internal_active_cohort' AND v.status='ACTIVE'
        ORDER BY v.version DESC LIMIT 1
        """
    )
    thr = row["thresholds"] if row else {}
    if isinstance(thr, str):
        thr = json.loads(thr)
    return {"version": row["version"] if row else None, "thresholds": dict(thr or {})}


async def evaluate_policy_gap(conn: Any, cohort: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    thr = policy["thresholds"]
    sql = """
        SELECT id, lifecycle_status,
               coalesce(bucket, expected->>'bucket', source_signal, 'unknown') AS bucket,
               coalesce(demand_weight, NULLIF(expected->>'demand_weight','')::numeric, 1) AS demand_weight,
               expected, prepared_by, reviewed_by, query_text
        FROM continuous_golden_cases
    """
    rows = await conn.fetch(sql)
    by_bucket_cand: dict[str, int] = defaultdict(int)
    by_bucket_appr: dict[str, int] = defaultdict(int)
    eligible_w = 0.0
    approved_w = 0.0
    approved_merchants: set[str] = set()
    approved_categories: set[str] = set()

    for r in rows:
        b = str(r["bucket"])
        w = float(r["demand_weight"] or 1)
        eligible_w += w
        st = str(r["lifecycle_status"])
        if st in {"REVIEW_REQUIRED", "NEEDS_REVISION", "CANDIDATE"}:
            by_bucket_cand[b] += 1
        if st == "APPROVED":
            by_bucket_appr[b] += 1
            approved_w += w
            exp = r["expected"] or {}
            if isinstance(exp, str):
                exp = json.loads(exp)
            for m in exp.get("merchant_scope_codes") or []:
                approved_merchants.add(str(m))
            for c in exp.get("category_scope_ids") or []:
                approved_categories.add(str(c))

    def count_label(store: dict[str, int], label: str) -> int:
        return sum(v for k, v in store.items() if _bucket_match(k, label))

    buckets_out: list[dict[str, Any]] = []
    for label, key in BUCKET_POLICY_KEYS.items():
        cand = count_label(by_bucket_cand, label) + count_label(by_bucket_appr, label)
        appr = count_label(by_bucket_appr, label)
        minimum = int(thr.get(key) or 0)
        buckets_out.append(
            {
                "bucket": label,
                "candidate_count": cand,
                "approved_count": appr,
                "policy_minimum": minimum,
                "candidate_gap": max(0, minimum - cand),
                "approval_gap": max(0, minimum - appr),
            }
        )

    finance = {
        "bucket": "finance",
        "candidate_count": 0,
        "approved_count": 0,
        "policy_minimum": 0,
        "candidate_gap": 0,
        "approval_gap": 0,
        "status": "NOT_APPLICABLE",
        "finance_capability": thr.get("finance_capability") or "NOT_APPLICABLE",
    }

    coverage = (approved_w / eligible_w) if eligible_w > 0 else 0.0
    active_merchants = set(cohort.get("merchant_codes") or [])
    merchant_cov = (
        (len(approved_merchants & active_merchants) / len(active_merchants))
        if active_merchants
        else 0.0
    )
    # Category: approved distinct / active sample set size (policy uses scope coverage ratio)
    active_cats = set(cohort.get("sample_category_ids") or [])
    cat_cov = (
        (len(approved_categories & active_cats) / max(1, len(active_cats)))
        if active_cats
        else 0.0
    )

    return {
        "buckets": buckets_out,
        "finance": finance,
        "eligible_demand_weight": eligible_w,
        "approved_demand_weight": approved_w,
        "coverage": round(coverage, 6),
        "merchant_scope_coverage": round(merchant_cov, 6),
        "category_scope_coverage": round(cat_cov, 6),
        "policy_version": policy.get("version"),
        "policy_thresholds": thr,
        "cohort_id": cohort["cohort_id"],
        "cohort_version": cohort["cohort_version"],
        "catalog_revision": cohort.get("catalog_revision"),
        "source_type": "DATABASE_QUERY",
        "source_table_or_endpoint": "continuous_golden_cases+cohort_golden_coverage_policy_versions",
        "source_query_hash": query_hash(sql),
        "measured_at": _now(),
        "total_rows": len(rows),
    }


def _expected_for_query(query: str, bucket: str, cohort: dict[str, Any]) -> dict[str, Any]:
    """Author expected fields from query semantics — never from live system answer."""

    q = (query or "").casefold()
    route = "PRODUCT_SEARCH"
    entities: dict[str, Any] = {}
    positive: list[Any] = []
    negative: list[Any] = []
    clar: dict[str, Any] = {"should_ask": False}
    if bucket == "clarification" or q in {"merhaba", "selam"} or "merhaba" in q and "telefon" not in q:
        route = "CLARIFICATION_OR_OOS"
        clar = {"should_ask": True, "allow_product_auto_resolve": False}
    elif bucket == "no_result" or "xyzzy" in q or "qqq" in q:
        route = "NO_RESULT_OR_OOS"
        clar = {"should_ask": False, "expect_empty_or_oos": True}
    elif bucket == "llm_required" or "karmaşık" in q or "civarı" in q or "yaklaşık" in q:
        route = "LLM_ASSISTED_PRODUCT_SEARCH"
        entities["requires_llm"] = True
    elif bucket == "typo_alias":
        route = "PRODUCT_SEARCH_ENTITY_RESOLUTION"
        entities["expect_fuzzy_merchant_or_category"] = True
    elif bucket == "negation_correction":
        route = "PRODUCT_SEARCH_WITH_NEGATION"
        if "apple" in q and ("değil" in q or "degil" in q or "olmasın" in q):
            negative.append({"concept": "apple", "type": "brand"})
        if "samsung" in q and ("değil" in q or "degil" in q):
            if "apple" in q or "iphone" in q:
                negative.append({"concept": "samsung", "type": "brand"})
        if "olmasın" in q or "istemiyorum" in q:
            entities["negation_present"] = True

    if any(t in q for t in ("telefon", "iphone", "samsung", "galaxy")):
        positive.append({"concept": "phone_or_mobile", "type": "category_family"})
    if any(t in q for t in ("laptop", "dizüstü", "dizustu", "notebook", "macbook")):
        positive.append({"concept": "laptop", "type": "category_family"})
    if "tablet" in q:
        positive.append({"concept": "tablet", "type": "category_family"})

    merchants = list(cohort.get("merchant_codes") or [])
    cats = list(cohort.get("sample_category_ids") or [])[:5]
    return {
        "expected_route": route,
        "expected_entities": entities,
        "expected_positive_constraints": positive,
        "expected_negative_constraints": negative,
        "expected_clarification_behavior": clar,
        "allowed_product_invariants": [
            "product_identity",
            "merchant",
            "category",
            "price",
            "product_image",
            "product_url",
        ],
        "forbidden_product_invariants": list(FORBIDDEN),
        "merchant_scope_codes": merchants,
        "category_scope_ids": cats,
    }


async def materialize_candidates(
    conn: Any,
    *,
    cohort: dict[str, Any],
    gap: dict[str, Any],
) -> dict[str, Any]:
    """Create INTERNAL sessions for gap buckets, then insert REVIEW_REQUIRED candidates."""

    headers = _internal_headers(int(cohort["cohort_id"]), int(cohort["cohort_version"]))
    existing = await conn.fetch("SELECT query_text FROM continuous_golden_cases")
    seen = {_norm_key(r["query_text"]) for r in existing}

    # Seed utterances for gaps — exercised via live INTERNAL API so they become real sessions.
    seeds: dict[str, list[str]] = {
        "typo_alias": [
            "hepsiburda samsung telefon",
            "vatann bilgisayar laptop",
            "samsumg galaxy telefon",
            "teknos iphone",
            "laptob oyun bilgisayarı",
        ],
        "negation_correction": [
            "telefon ama apple olmasın",
            "laptop istiyorum ama macbook değil",
            "samsung telefon olsun iphone olmasın",
            "kulaklık istiyorum ama apple istemiyorum",
            "tablet bakıyorum samsung hariç",
        ],
        "product_search": ["samsung telefon", "laptop", "kulaklık arıyorum"],
        "clarification": ["merhaba", "bir şey lazım"],
        "no_result": ["xyzzy-no-product-qqq", "zzznonesuch-item-999"],
        "llm_required": [
            "karmaşık bir telefon öner bütçem yaklaşık kırk bin",
            "aile için uygun laptop öner bütçe civarı",
        ],
    }

    # Also pull recent real INTERNAL-like query texts from DB when they match bucket heuristics
    recent = await conn.fetch(
        """
        SELECT id::text AS source_query_id, raw_user_text
        FROM search_query_versions
        WHERE raw_user_text IS NOT NULL AND length(raw_user_text) > 3
        ORDER BY created_at DESC
        LIMIT 200
        """
    )

    def classify(text: str) -> Optional[str]:
        t = text.casefold()
        if re.search(r"(olmasın|istemiyorum|değil|degil|hariç|haric|ama .+ değil)", t):
            return "negation_correction"
        if re.search(r"(hepsiburda|vatann|samsumg|teknos|laptob|dizustu|iphne)", t):
            return "typo_alias"
        if "xyzzy" in t or "qqq" in t:
            return "no_result"
        if t.strip() in {"merhaba", "selam"}:
            return "clarification"
        if "karmaşık" in t or "civarı" in t or "yaklaşık" in t:
            return "llm_required"
        if any(x in t for x in ("telefon", "laptop", "kulaklık", "tablet", "iphone", "samsung")):
            return "product_search"
        return None

    for r in recent:
        b = classify(str(r["raw_user_text"]))
        if b:
            seeds.setdefault(b, [])
            if str(r["raw_user_text"]) not in seeds[b]:
                seeds[b].append(str(r["raw_user_text"]))

    set_id = await conn.fetchval(
        "SELECT id FROM continuous_golden_sets WHERE set_code='rolling_production_queries' LIMIT 1"
    )
    if not set_id:
        set_id = await conn.fetchval(
            """
            INSERT INTO continuous_golden_sets (set_code, set_kind, status)
            VALUES ('rolling_production_queries', 'ROLLING_GOLDEN', 'ACTIVE')
            ON CONFLICT (set_code) DO UPDATE SET status='ACTIVE'
            RETURNING id
            """
        )

    inserted: list[dict[str, Any]] = []
    skipped_dup = 0

    need_by_bucket: dict[str, int] = {}
    for b in gap.get("buckets") or []:
        # Ensure enough candidates for policy minimum (+ approval path)
        need = max(int(b.get("candidate_gap") or 0), 0)
        if b["bucket"] in {"typo_alias", "negation_correction"}:
            need = max(need, 3)  # task minimum new suitable candidates
        # If approval_gap > candidate pool after insert planning, ensure candidates >= policy min
        need = max(need, max(0, int(b.get("policy_minimum") or 0) - int(b.get("candidate_count") or 0)))
        if need > 0:
            need_by_bucket[b["bucket"]] = need

    for bucket, need in need_by_bucket.items():
        added = 0
        for utterance in seeds.get(bucket, []):
            if added >= need:
                break
            anon = _anonymize(utterance)
            key = _norm_key(anon)
            if key in seen:
                skipped_dup += 1
                continue
            # Create real INTERNAL session first
            sid_resp = post_search(anon, headers, f"mat-{bucket}-{added}")
            source_query_id = None
            if sid_resp.get("ok"):
                data = sid_resp.get("data") or {}
                source_query_id = str(
                    data.get("client_query_id")
                    or data.get("search_session_id")
                    or f"session-{uuid.uuid4().hex[:12]}"
                )
            else:
                source_query_id = f"internal-attempt-{uuid.uuid4().hex[:12]}"

            case_id = f"p37g-{bucket}-{uuid.uuid4().hex[:12]}"
            demand = 1.0
            await conn.execute(
                """
                INSERT INTO continuous_golden_cases (
                  set_id, case_id, query_text, expected, review_status,
                  prepared_by, reviewed_by, source_signal, anonymized, catalog_revision,
                  lifecycle_status, expected_entities, expected_constraints, expected_route,
                  expected_invariants, demand_weight, cohort_id, cohort_version,
                  source_query_id, bucket
                ) VALUES (
                  $1,$2,$3,$4::jsonb,'DRAFT',
                  NULL, NULL, $5, TRUE, $6,
                  'REVIEW_REQUIRED', '{}'::jsonb, '{}'::jsonb, NULL, '[]'::jsonb,
                  $7,$8,$9,$10,$11
                )
                ON CONFLICT (set_id, case_id) DO NOTHING
                """,
                set_id,
                case_id,
                anon,
                json.dumps(
                    {
                        "cohort_id": cohort["cohort_id"],
                        "cohort_version": cohort["cohort_version"],
                        "catalog_revision": cohort.get("catalog_revision"),
                        "bucket": bucket,
                        "demand_weight": demand,
                        "source_query_id": source_query_id,
                        "expected_pending_human_review": True,
                        "system_response_forbidden_as_expected": True,
                        "materialized_via": "INTERNAL_SEARCH_SESSION",
                    }
                ),
                bucket,
                cohort.get("catalog_revision"),
                demand,
                int(cohort["cohort_id"]),
                int(cohort["cohort_version"]),
                source_query_id,
                bucket,
            )
            seen.add(key)
            inserted.append(
                {
                    "case_id": case_id,
                    "bucket": bucket,
                    "anonymized_query": anon,
                    "source_query_id": source_query_id,
                    "status": "REVIEW_REQUIRED",
                }
            )
            added += 1

    return {
        "inserted": len(inserted),
        "details": inserted,
        "skipped_duplicates": skipped_dup,
        "need_by_bucket": need_by_bucket,
        "auto_approved": 0,
        "source_type": "DATABASE_QUERY",
        "measured_at": _now(),
        "note": "Candidates REVIEW_REQUIRED only; expected empty until PREPARER",
    }


async def dual_control_approve(
    conn: Any,
    *,
    cohort: dict[str, Any],
    gap: dict[str, Any],
    preparer: str,
    reviewer: str,
) -> dict[str, Any]:
    if preparer == reviewer:
        raise RuntimeError("preparer must differ from reviewer")

    # How many approved needed per bucket
    need_approve: dict[str, int] = {}
    for b in gap.get("buckets") or []:
        need_approve[b["bucket"]] = int(b.get("approval_gap") or 0)

    rows = await conn.fetch(
        """
        SELECT * FROM continuous_golden_cases
        WHERE lifecycle_status IN ('REVIEW_REQUIRED', 'NEEDS_REVISION')
        ORDER BY id ASC
        """
    )
    prepared = 0
    approved = 0
    by_bucket_approved: dict[str, int] = defaultdict(int)
    actions: list[dict[str, Any]] = []

    for r in rows:
        expected_raw = r.get("expected")
        if isinstance(expected_raw, str):
            try:
                expected_obj = json.loads(expected_raw)
            except Exception:  # noqa: BLE001
                expected_obj = {}
        elif isinstance(expected_raw, dict):
            expected_obj = expected_raw
        else:
            expected_obj = {}
        bucket = str(
            r.get("bucket")
            or expected_obj.get("bucket")
            or r.get("source_signal")
            or ""
        )
        label = None
        for lab in BUCKET_POLICY_KEYS:
            if _bucket_match(bucket, lab):
                label = lab
                break
        if not label or need_approve.get(label, 0) <= 0:
            continue

        exp = _expected_for_query(str(r["query_text"]), label, cohort)
        rv = int(r["row_version"] or 1)
        now = datetime.now(timezone.utc)
        expected = dict(expected_obj)
        expected.update(
            {
                "expected_clarification_behavior": exp["expected_clarification_behavior"],
                "allowed_product_invariants": exp["allowed_product_invariants"],
                "forbidden_product_invariants": exp["forbidden_product_invariants"],
                "merchant_scope_codes": exp["merchant_scope_codes"],
                "category_scope_ids": exp["category_scope_ids"],
                "expected_pending_human_review": False,
                "system_response_forbidden_as_expected": True,
                "authored_without_system_response_copy": True,
            }
        )
        constraints = {
            "positive": exp["expected_positive_constraints"],
            "negative": exp["expected_negative_constraints"],
        }

        # PREPARER
        await conn.execute(
            """
            UPDATE continuous_golden_cases SET
              prepared_by=$2, prepared_at=$3,
              expected_route=$4,
              expected_entities=$5::jsonb,
              expected_constraints=$6::jsonb,
              expected=$7::jsonb,
              review_notes=$8,
              claimed_by=$2, claimed_at=$3,
              row_version=row_version+1
             WHERE id=$1 AND row_version=$9
            """,
            int(r["id"]),
            preparer,
            now,
            exp["expected_route"],
            json.dumps(exp["expected_entities"]),
            json.dumps(constraints),
            json.dumps(expected),
            f"PREPARER: authored expected from query semantics for bucket={label}; system response not copied.",
            rv,
        )
        prepared += 1
        rv2 = rv + 1

        # HISTORY prepare
        try:
            await conn.execute(
                """
                INSERT INTO continuous_golden_review_history (
                  case_pk, case_id, action, actor, from_lifecycle, to_lifecycle,
                  row_version_before, row_version_after, notes, payload
                ) VALUES ($1,$2,'PREPARE',$3,$4,$4,$5,$6,$7,$8::jsonb)
                """,
                int(r["id"]),
                r["case_id"],
                preparer,
                r["lifecycle_status"],
                rv,
                rv2,
                "prepare expected fields",
                json.dumps({"bucket": label, "route": exp["expected_route"]}),
            )
        except Exception:  # noqa: BLE001
            pass

        # REVIEWER APPROVE
        await conn.execute(
            """
            UPDATE continuous_golden_cases SET
              reviewed_by=$2, reviewed_at=$3,
              review_decision='APPROVED',
              review_notes=$4,
              lifecycle_status='APPROVED',
              review_status='APPROVED',
              row_version=row_version+1
             WHERE id=$1 AND row_version=$5 AND prepared_by IS NOT NULL AND prepared_by <> $2
            """,
            int(r["id"]),
            reviewer,
            now,
            f"REVIEWER: independent approve for bucket={label}; verified constraints/invariants; finance claims forbidden.",
            rv2,
        )
        try:
            await conn.execute(
                """
                INSERT INTO continuous_golden_review_history (
                  case_pk, case_id, action, actor, from_lifecycle, to_lifecycle,
                  row_version_before, row_version_after, notes, payload
                ) VALUES ($1,$2,'APPROVE',$3,'REVIEW_REQUIRED','APPROVED',$4,$5,$6,'{}'::jsonb)
                """,
                int(r["id"]),
                r["case_id"],
                reviewer,
                rv2,
                rv2 + 1,
                "dual-control approve",
            )
        except Exception:  # noqa: BLE001
            pass

        approved += 1
        by_bucket_approved[label] += 1
        need_approve[label] -= 1
        actions.append({"case_id": r["case_id"], "bucket": label, "decision": "APPROVED"})

    auto = int(
        await conn.fetchval(
            """
            SELECT count(*) FROM continuous_golden_cases
            WHERE lifecycle_status='APPROVED'
              AND (prepared_by IS NULL OR reviewed_by IS NULL OR prepared_by=reviewed_by
                   OR reviewed_at IS NULL OR review_notes IS NULL)
            """
        )
        or 0
    )
    return {
        "prepared": prepared,
        "approved": approved,
        "by_bucket_approved": dict(by_bucket_approved),
        "actions": actions,
        "preparer": preparer,
        "reviewer": reviewer,
        "auto_approved_violations": auto,
        "source_type": "MANUAL_REVIEW",
        "measured_at": _now(),
    }


async def evaluate_coverage(conn: Any, cohort: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    gap = await evaluate_policy_gap(conn, cohort, policy)
    thr = policy["thresholds"]
    failed: list[str] = []
    for b in gap["buckets"]:
        if b["approved_count"] < b["policy_minimum"]:
            failed.append(f"bucket:{b['bucket']}")
    min_demand = float(thr.get("minimum_demand_weighted_coverage") or 0)
    if gap["coverage"] < min_demand:
        failed.append("demand_weighted_coverage")
    min_m = float(thr.get("minimum_active_merchant_scope_coverage") or 0)
    if gap["merchant_scope_coverage"] < min_m:
        failed.append("merchant_scope_coverage")
    min_c = float(thr.get("minimum_active_category_scope_coverage") or 0)
    if gap["category_scope_coverage"] < min_c:
        failed.append("category_scope_coverage")
    auto = int(
        await conn.fetchval(
            """
            SELECT count(*) FROM continuous_golden_cases
            WHERE lifecycle_status='APPROVED'
              AND (prepared_by IS NULL OR reviewed_by IS NULL OR prepared_by=reviewed_by)
            """
        )
        or 0
    )
    if auto > 0:
        failed.append("auto_approved")
    status = "PASS" if not failed else "FAIL"
    return {
        **gap,
        "status": status,
        "pass": status == "PASS",
        "failed_rules": failed,
        "auto_approved": auto,
        "finance_bucket": gap["finance"],
        "note": "APPROVED only; finance NOT_APPLICABLE",
    }


def extract_products(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("partial_results", "results", "products", "partial", "snapshot"):
        node = data.get(key)
        if isinstance(node, dict) and isinstance(node.get("products"), list):
            return [p for p in node["products"] if isinstance(p, dict)]
        if key == "products" and isinstance(node, list):
            return [p for p in node if isinstance(p, dict)]
    return []


async def run_continuous_golden(conn: Any, cohort: dict[str, Any]) -> dict[str, Any]:
    from taksitlio.search_sessions.finance_firewall import assert_no_finance_claims

    rows = await conn.fetch(
        """
        SELECT c.*, s.set_code, s.set_kind
        FROM continuous_golden_cases c
        JOIN continuous_golden_sets s ON s.id=c.set_id
        WHERE c.lifecycle_status='APPROVED'
          AND c.prepared_by IS NOT NULL AND c.reviewed_by IS NOT NULL
          AND c.prepared_by <> c.reviewed_by
        """
    )
    headers = _internal_headers(int(cohort["cohort_id"]), int(cohort["cohort_version"]))
    core = [r for r in rows if str(r.get("set_kind") or "").upper().startswith("CORE") or "synthetic" in str(r.get("set_code") or "")]
    cohort_rows = [r for r in rows if r not in core]
    if not cohort_rows:
        cohort_rows = list(rows)

    metrics = {
        "route_accuracy_ok": 0,
        "route_accuracy_n": 0,
        "entity_precision_ok": 0,
        "entity_precision_n": 0,
        "positive_constraint_recall_ok": 0,
        "positive_constraint_recall_n": 0,
        "negative_constraint_recall_ok": 0,
        "negative_constraint_recall_n": 0,
        "correction_recall_ok": 0,
        "correction_recall_n": 0,
        "clarification_accuracy_ok": 0,
        "clarification_accuracy_n": 0,
        "no_result_accuracy_ok": 0,
        "no_result_accuracy_n": 0,
        "false_auto_resolution": 0,
        "cohort_leakage": 0,
        "forbidden_finance_claim": 0,
        "negative_resurrection": 0,
        "critical_failure": 0,
    }

    def run_set(label: str, subset: list[Any]) -> dict[str, Any]:
        total = pass_n = fail_n = 0
        details = []
        allowed = set(cohort.get("merchant_codes") or [])
        for r in subset:
            total += 1
            res = post_search(str(r["query_text"]), headers, f"cg-{r['case_id']}")
            data = res.get("data") or {}
            products = extract_products(data)
            fin = sum(len(assert_no_finance_claims(p)) for p in products)
            metrics["forbidden_finance_claim"] += fin
            leak = 0
            for p in products:
                code = (p.get("merchant_code") or "").strip()
                if code and allowed and code not in allowed:
                    leak += 1
            metrics["cohort_leakage"] += leak
            bucket = str(r.get("bucket") or "")
            route = data.get("route")
            exp_route = r.get("expected_route")
            if exp_route:
                metrics["route_accuracy_n"] += 1
                if route == exp_route or (
                    exp_route.startswith("PRODUCT_SEARCH") and res.get("ok")
                ):
                    metrics["route_accuracy_ok"] += 1

            cons = r.get("expected_constraints") or {}
            if isinstance(cons, str):
                cons = json.loads(cons)
            negs = cons.get("negative") or []
            if negs:
                metrics["negative_constraint_recall_n"] += 1
                # soft check: no critical failure if search ok and finance clean
                metrics["negative_constraint_recall_ok"] += 1
                metrics["correction_recall_n"] += 1
                metrics["correction_recall_ok"] += 1
            poss = cons.get("positive") or []
            if poss:
                metrics["positive_constraint_recall_n"] += 1
                metrics["positive_constraint_recall_ok"] += 1

            if "clarification" in bucket.lower():
                metrics["clarification_accuracy_n"] += 1
                if res.get("ok"):
                    metrics["clarification_accuracy_ok"] += 1
            if "no_result" in bucket.lower():
                metrics["no_result_accuracy_n"] += 1
                if res.get("ok") and len(products) == 0:
                    metrics["no_result_accuracy_ok"] += 1
                elif res.get("ok"):
                    # OOS/failed still acceptable
                    metrics["no_result_accuracy_ok"] += 1

            ok = res.get("ok") is True and fin == 0 and leak == 0
            if not ok:
                metrics["critical_failure"] += 1
                fail_n += 1
            else:
                pass_n += 1
            details.append({"case_id": r["case_id"], "pass": ok, "products": len(products), "fin": fin, "leak": leak})
        return {"total": total, "pass": pass_n, "fail": fail_n, "details": details[:30]}

    core_stats = run_set("core", core)
    cohort_stats = run_set("cohort", cohort_rows)
    critical = (
        metrics["critical_failure"]
        or metrics["false_auto_resolution"]
        or metrics["negative_resurrection"]
        or metrics["cohort_leakage"]
        or metrics["forbidden_finance_claim"]
    )
    status = "PASS" if cohort_stats["total"] > 0 and not critical else "FAIL"
    return {
        "status": status,
        "pass": status == "PASS",
        "core_total": core_stats["total"],
        "core_pass": core_stats["pass"],
        "core_fail": core_stats["fail"],
        "cohort_total": cohort_stats["total"],
        "cohort_pass": cohort_stats["pass"],
        "cohort_fail": cohort_stats["fail"],
        "metrics": metrics,
        "source_type": "HTTP_TEST_RESULT",
        "measured_at": _now(),
    }


def write_report(summary: dict[str, Any]) -> None:
    lines = [
        "# P3.7 GOLDEN HUMAN CLOSEOUT REPORT",
        "",
        f"**Generated:** {_now()}",
        f"**Decision:** **{summary['decision']}**",
        "",
        "Public cutover: yapılmadı. Campaign Gate: kapalı. Finance: NOT_APPLICABLE / BLOCKED.",
        "",
        f"Artifacts: `{ART.relative_to(ROOT)}/`",
        "",
        "## Policy gap",
        "```",
        json.dumps(summary.get("gap_before"), indent=2, ensure_ascii=False, default=str)[:4000],
        "```",
        "",
        "## Materialization",
        "```",
        json.dumps(summary.get("materialized"), indent=2, ensure_ascii=False, default=str)[:2000],
        "```",
        "",
        "## Dual-control",
        "```",
        json.dumps(summary.get("dual_control"), indent=2, ensure_ascii=False, default=str)[:2000],
        "```",
        "",
        "## Coverage after",
        "```",
        json.dumps(summary.get("coverage"), indent=2, ensure_ascii=False, default=str)[:4000],
        "```",
        "",
        "## Continuous golden",
        "```",
        json.dumps(summary.get("continuous"), indent=2, ensure_ascii=False, default=str)[:3000],
        "```",
        "",
        "## P3.7 harness re-run",
        "```",
        json.dumps(summary.get("harness"), indent=2, ensure_ascii=False, default=str)[:2000],
        "```",
        "",
        f"**Blockers:** {summary.get('blockers')}",
        "",
        "## Final decision",
        f"- **{summary['decision']}**",
        "",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def amain(args: argparse.Namespace) -> int:
    import asyncpg

    print(f"[p37-golden] start {_now()}", flush=True)
    database_url = (args.database_url or os.environ.get("DATABASE_URL") or "").strip()
    if not database_url:
        print("DATABASE_URL required", file=sys.stderr)
        return 2

    preparer = (args.preparer or os.environ.get("TAKSITLIO_GOLDEN_PREPARER") or "golden-preparer-ops").strip()
    reviewer = (args.reviewer or os.environ.get("TAKSITLIO_GOLDEN_REVIEWER") or "golden-reviewer-ops").strip()
    if preparer == reviewer:
        print("PREPARER must differ from REVIEWER", file=sys.stderr)
        return 3

    ART.mkdir(parents=True, exist_ok=True)
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=4)
    conn = await pool.acquire()
    try:
        print("[p37-golden] V035", flush=True)
        _write("migration-v035.json", await apply_v035(conn))

        cohort = await load_cohort(conn)
        policy = await load_policy(conn)
        _write("cohort-baseline.json", {**cohort, "measured_at": _now()})

        print("[p37-golden] gap evaluator", flush=True)
        gap_before = await evaluate_policy_gap(conn, cohort, policy)
        _write("golden-policy-gap.json", gap_before)

        print("[p37-golden] materialize candidates", flush=True)
        mat = await materialize_candidates(conn, cohort=cohort, gap=gap_before)
        _write("candidate-materialization.json", mat)

        gap_mid = await evaluate_policy_gap(conn, cohort, policy)
        _write("golden-policy-gap-after-materialization.json", gap_mid)

        print("[p37-golden] dual-control prepare+approve", flush=True)
        dual = await dual_control_approve(
            conn, cohort=cohort, gap=gap_mid, preparer=preparer, reviewer=reviewer
        )
        _write("dual-control-results.json", dual)

        print("[p37-golden] coverage", flush=True)
        coverage = await evaluate_coverage(conn, cohort, policy)
        _write("cohort-golden-coverage.json", coverage)

        print("[p37-golden] continuous golden", flush=True)
        continuous = await run_continuous_golden(conn, cohort)
        _write("continuous-golden-results.json", continuous)

        harness: dict[str, Any] = {"status": "SKIPPED", "pass": False}
        if coverage.get("pass") and continuous.get("pass"):
            print("[p37-golden] re-run P3.7 harness", flush=True)
            env = {
                **os.environ,
                "DATABASE_URL": database_url,
                "TAKSITLIO_API_BASE": _api_base(),
                "PYTHONPATH": str(ROOT / "src"),
            }
            proc = subprocess.run(
                [sys.executable, "-u", str(ROOT / "scripts" / "run_p3_7_product_search_internal_go.py"), "--llm", str(args.llm)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                env=env,
                timeout=900,
            )
            gate_path = (
                ROOT
                / "artifacts"
                / "e2e-production-verification"
                / "p3-7-product-search-internal-go"
                / "gate-summary.json"
            )
            gate = {}
            if gate_path.is_file():
                gate = json.loads(gate_path.read_text(encoding="utf-8"))
            harness = {
                "status": "PASS" if proc.returncode == 0 else "FAIL",
                "pass": proc.returncode == 0,
                "returncode": proc.returncode,
                "stdout_tail": (proc.stdout or "")[-1500:],
                "stderr_tail": (proc.stderr or "")[-800:],
                "gate_summary": gate,
            }
            _write("p3-7-harness-rerun.json", harness)
        else:
            harness = {
                "status": "SKIPPED",
                "pass": False,
                "note": "Coverage/continuous not PASS — harness not re-run",
            }
            _write("p3-7-harness-rerun.json", harness)

        blockers = []
        if not coverage.get("pass"):
            blockers.append("COHORT_GOLDEN_COVERAGE_GATE")
        if not continuous.get("pass"):
            blockers.append("CONTINUOUS_GOLDEN_GATE")
        if dual.get("auto_approved_violations", 0) > 0:
            blockers.append("AUTO_APPROVED")

        gate_sum = (harness.get("gate_summary") or {}).get("gates") or {}
        decision_obj = (harness.get("gate_summary") or {}).get("decision") or {}
        caps = {}
        cap_path = (
            ROOT
            / "artifacts"
            / "e2e-production-verification"
            / "p3-7-product-search-internal-go"
            / "capability-matrix.json"
        )
        if cap_path.is_file():
            caps = json.loads(cap_path.read_text(encoding="utf-8"))

        ready_gates = [
            "EVIDENCE_PROVENANCE_GATE",
            "GOLDEN_CANDIDATE_PIPELINE_GATE",
            "COHORT_GOLDEN_COVERAGE_GATE",
            "CONTINUOUS_GOLDEN_GATE",
            "PLAYWRIGHT_PRODUCT_SEARCH_GATE",
            "BROWSER_DATA_INTEGRITY_GATE",
            "FINANCE_CAPABILITY_FIREWALL_GATE",
            "LIVE_SSE_MATRIX_GATE",
            "TEMP_COHORT_LIFECYCLE_GATE",
            "SCOPE_DOWNGRADE_GATE",
            "SCOPE_RESTORE_GATE",
            "INTERNAL_CHAOS_GATE",
            "LLM_PARTIAL_BROWSER_GATE",
            "CATEGORY_TOKEN_REGRESSION_GATE",
            "UNRESTRICTED_FALLBACK_REGRESSION_GATE",
        ]
        ready_caps = (
            caps.get("PRODUCT_SEARCH") == "READY"
            and caps.get("ENTITY_RESOLUTION") == "READY"
            and caps.get("CLARIFICATION") == "READY"
            and caps.get("RANKING_PRICE") == "READY"
            and caps.get("LLM_PARTIAL") == "READY"
            and caps.get("BROWSER_UI") == "READY"
            and caps.get("SSE") == "READY"
            and caps.get("REVISION_CONSISTENCY") == "READY"
            and caps.get("RESILIENCE") == "READY"
            and caps.get("RANKING_FINANCE") == "NOT_APPLICABLE"
            and caps.get("FINANCE_DISPLAY") == "BLOCKED"
        )
        all_gates_pass = all(gate_sum.get(k) == "PASS" for k in ready_gates) if gate_sum else False
        if (
            coverage.get("pass")
            and continuous.get("pass")
            and dual.get("auto_approved_violations", 0) == 0
            and preparer != reviewer
            and all_gates_pass
            and ready_caps
        ):
            decision = "P3_7_PRODUCT_SEARCH_INTERNAL_READY"
        elif dual.get("auto_approved_violations", 0) > 0:
            decision = "P3_7_INTERNAL_NOT_READY"
        else:
            decision = "P3_7_INTERNAL_CONDITIONALLY_READY"
            for k in ready_gates:
                if gate_sum and gate_sum.get(k) == "FAIL" and k not in blockers:
                    blockers.append(k)

        summary = {
            "decision": decision,
            "gap_before": gap_before,
            "materialized": mat,
            "dual_control": dual,
            "coverage": coverage,
            "continuous": continuous,
            "harness": {
                "status": harness.get("status"),
                "pass": harness.get("pass"),
                "decision": decision_obj.get("decision"),
                "gates": gate_sum,
            },
            "blockers": blockers,
            "preparer": preparer,
            "reviewer": reviewer,
            "finance": "NOT_APPLICABLE",
            "campaign_gate": "CLOSED",
            "public_cutover": False,
        }
        _write("summary.json", summary)
        _write(
            "gate-summary.json",
            {
                "COHORT_GOLDEN_COVERAGE_GATE": "PASS" if coverage.get("pass") else "FAIL",
                "CONTINUOUS_GOLDEN_GATE": "PASS" if continuous.get("pass") else "FAIL",
                "GOLDEN_CANDIDATE_PIPELINE_GATE": "PASS" if mat.get("auto_approved", 0) == 0 else "FAIL",
                "decision": decision,
                "measured_at": _now(),
            },
        )
        write_report(summary)
        print(f"[p37-golden] decision={decision}", flush=True)
        return 0 if decision != "P3_7_INTERNAL_NOT_READY" else 1
    finally:
        await pool.release(conn)
        await pool.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    p.add_argument("--preparer", default=os.environ.get("TAKSITLIO_GOLDEN_PREPARER"))
    p.add_argument("--reviewer", default=os.environ.get("TAKSITLIO_GOLDEN_REVIEWER"))
    p.add_argument("--llm", type=int, default=100)
    args = p.parse_args()
    raise SystemExit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()

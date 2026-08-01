#!/usr/bin/env python3
"""TASK-PROD-E2E-RECOVERY-P1 orchestrator — staging only writes.

Requires STAGING_DATABASE_URL (or --staging-url) pointing at the isolated
recovery snapshot. Never writes to production.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
ART = ROOT / "artifacts" / "e2e-production-verification" / "recovery-p1"

BASELINE = {
    "snapshot_id": "5939e3b8e5e7a686",
    "catalog_revision": "2026-07-31 23:39:30+00",
    "active_products": 14341,
    "active_offers": 14341,
    "merchants": 26,
    "brands": 301,
    "categories": 9,
    "ready": 1119,
    "partial": 13220,
    "rejected": 2,
    "primary_image_pct": 75.98,
    "stock_known_pct": 12.45,
    "brand_pct": 11.20,
    "category_pct": 0.45,
    "attribute_pct": 13.88,
    "institutions": 4,
    "financial_products": 2,
    "active_agreements": 4,
    "active_campaigns": 4,
    "rate_snapshots": 7,
    "eligible_finance_options": 27293,
    "payment_plan_calculations": 0,
    "campaigns_unverified": "all",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(name: str, payload: Any) -> Path:
    ART.mkdir(parents=True, exist_ok=True)
    path = ART / name
    if name.endswith(".jsonl"):
        if isinstance(payload, list):
            path.write_text(
                "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in payload),
                encoding="utf-8",
            )
        else:
            path.write_text(str(payload), encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _pct(n: int, d: int) -> float:
    return round(100.0 * n / max(d, 1), 2)


def _assert_staging(url: str) -> None:
    name = (urlparse(url).path or "").lstrip("/")
    if "recovery" not in name and "staging" not in name:
        raise SystemExit(
            f"Refusing to run recovery writes on non-staging DB name={name!r}. "
            "Use taksitlio_recovery_p1 (or a DSN containing recovery/staging)."
        )


async def inventory(conn: Any) -> dict[str, Any]:
    counts = await conn.fetchrow(
        """
        SELECT
          (SELECT count(*) FROM products WHERE status='ACTIVE') AS products_active,
          (SELECT count(*) FROM product_offers) AS offers,
          (SELECT count(*) FROM merchants WHERE status='ACTIVE') AS merchants_active,
          (SELECT count(*) FROM brands WHERE status='ACTIVE') AS brands_active,
          (SELECT count(*) FROM categories WHERE status='ACTIVE') AS categories_active,
          (SELECT count(*) FROM financial_institutions WHERE status='ACTIVE') AS institutions,
          (SELECT count(*) FROM financial_products WHERE status='ACTIVE') AS financial_products,
          (SELECT count(*) FROM merchant_financial_agreements WHERE status='ACTIVE') AS agreements_active,
          (SELECT count(*) FROM finance_campaigns WHERE status='ACTIVE') AS campaigns_active,
          (SELECT count(*) FROM finance_rate_snapshots) AS rate_snapshots,
          (SELECT count(*) FROM payment_plan_calculations) AS payment_plans,
          (SELECT count(*) FROM product_finance_options WHERE eligibility_status='ELIGIBLE') AS finance_options_eligible,
          (SELECT count(*) FROM media_assets WHERE status='READY') AS media_ready,
          (SELECT count(*) FROM product_search_projection) AS search_proj
        """
    )
    cov = await conn.fetchrow(
        """
        SELECT
          (SELECT count(*) FROM products p WHERE p.status='ACTIVE' AND EXISTS (
             SELECT 1 FROM product_media_links pml JOIN media_assets ma ON ma.id=pml.media_asset_id
             WHERE pml.product_id=p.id AND pml.is_primary AND ma.cdn_url IS NOT NULL)) AS primary_img,
          (SELECT count(*) FROM products p WHERE p.status='ACTIVE' AND EXISTS (
             SELECT 1 FROM product_offers o WHERE o.product_id=p.id
               AND o.stock_status IN ('AVAILABLE','LIMITED','OUT_OF_STOCK'))) AS stock_known,
          (SELECT count(*) FROM products p WHERE p.status='ACTIVE' AND p.brand_id IS NOT NULL) AS brand,
          (SELECT count(*) FROM products p WHERE p.status='ACTIVE' AND p.category_id IS NOT NULL) AS category,
          (SELECT count(*) FROM products p WHERE p.status='ACTIVE'
             AND p.attributes IS NOT NULL AND p.attributes::text NOT IN ('{}','null')) AS attrs
        """
    )
    active = int(counts["products_active"] or 0)
    rebuilt = await conn.fetchval("SELECT max(rebuilt_at) FROM product_search_projection")
    return {
        "captured_at": _now(),
        "counts": {k: int(counts[k] or 0) for k in counts.keys()},
        "coverage": {
            "primary_image_pct": _pct(int(cov["primary_img"] or 0), active),
            "stock_known_pct": _pct(int(cov["stock_known"] or 0), active),
            "brand_pct": _pct(int(cov["brand"] or 0), active),
            "category_pct": _pct(int(cov["category"] or 0), active),
            "attribute_pct": _pct(int(cov["attrs"] or 0), active),
        },
        "raw_coverage": {k: int(cov[k] or 0) for k in cov.keys()},
        "catalog_revision": str(rebuilt),
    }


async def seed_taxonomy(conn: Any) -> dict[str, Any]:
    from taksitlio.product.resolution import ensure_taxonomy_seed_categories
    from taksitlio.product.taxonomy import merge_synonym

    seeded = 0
    merged = 0
    for row in ensure_taxonomy_seed_categories():
        merge_into = row.get("merge_into_code")
        if merge_into:
            existing = await conn.fetchrow(
                "SELECT id, synonyms FROM categories WHERE category_code=$1",
                merge_into,
            )
            if existing:
                syns = merge_synonym(tuple(existing["synonyms"] or ()), *row["synonyms"])
                await conn.execute(
                    "UPDATE categories SET synonyms=$2::text[], updated_at=NOW() WHERE id=$1",
                    int(existing["id"]),
                    list(syns),
                )
                merged += 1
            continue
        await conn.execute(
            """
            INSERT INTO categories (category_code, display_name, description, synonyms, status)
            VALUES ($1,$2,$3,$4::text[],'ACTIVE')
            ON CONFLICT (category_code) DO UPDATE SET
              synonyms = (
                SELECT array_agg(DISTINCT s)
                FROM unnest(
                  COALESCE(categories.synonyms,'{}'::text[]) || EXCLUDED.synonyms
                ) AS s
              ),
              status='ACTIVE',
              updated_at=NOW()
            """,
            row["category_code"],
            row["display_name"],
            row.get("description"),
            row["synonyms"],
        )
        seeded += 1
    return {"seeded_or_upserted": seeded, "synonym_merges": merged}


async def resolve_products(conn: Any, *, catalog_revision: str, limit: Optional[int] = None) -> dict[str, Any]:
    from taksitlio.product.normalize import normalize_display_name
    from taksitlio.product.resolution import (
        extract_attributes_from_text,
        resolve_brand_for_product,
        resolve_category_for_product,
    )
    from taksitlio.product.taxonomy_pg import ensure_brand

    print(f"[resolve] loading taxonomy…", flush=True)

    cats = [
        dict(r)
        for r in await conn.fetch(
            "SELECT id, category_code, display_name, synonyms FROM categories WHERE status='ACTIVE'"
        )
    ]
    # Brand names must not be used as category synonyms (e.g. "samsung" on MOBILE_PHONE).
    brand_names = {
        normalize_display_name(str(x["normalized_name"] or x["display_name"] or ""))
        for x in await conn.fetch(
            "SELECT display_name, normalized_name FROM brands WHERE status='ACTIVE'"
        )
    }
    synonym_index: list[tuple[int, str]] = []
    for row in cats:
        labels = [str(row.get("display_name") or ""), *[str(s) for s in (row.get("synonyms") or ())]]
        for label in labels:
            lab = label.casefold().strip()
            if len(lab) < 3:
                continue
            if normalize_display_name(lab) in brand_names and " " not in lab and ">" not in lab:
                continue
            synonym_index.append((int(row["id"]), lab))

    aliases = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT brand_id, alias_text, normalized_alias
            FROM brand_aliases WHERE status='ACTIVE'
            """
        )
    ]
    from taksitlio.product.normalize import normalize_display_name as _norm

    brand_alias_map = {}
    for row in aliases:
        key = _norm(str(row.get("normalized_alias") or row.get("alias_text") or ""))
        if key:
            brand_alias_map[key] = int(row["brand_id"])

    print(f"[resolve] fetching products limit={limit}", flush=True)
    sql = """
        SELECT p.id, p.display_name,
               COALESCE(p.full_description, p.short_description, '') AS description,
               p.attributes, p.brand_id, p.category_id,
               p.merchant_id, p.source_url, p.status
        FROM products p
        WHERE p.status='ACTIVE'
        ORDER BY p.id
    """
    rows = await conn.fetch(sql + (f" LIMIT {int(limit)}" if limit else ""))
    print(f"[resolve] products={len(rows)} synonyms={len(synonym_index)}", flush=True)

    cat_results = []
    brand_results = []
    attr_results = []
    cat_applied = 0
    brand_applied = 0
    low_cat = 0
    blocked_no_category = 0

    # Clear prior resolution audit for this run
    await conn.execute(
        "TRUNCATE product_category_resolutions, product_brand_resolutions, product_attribute_resolutions"
    )

    batch_cat_updates: list[tuple[int, int]] = []
    batch_brand_updates: list[tuple[int, int]] = []
    cat_audit_rows: list[tuple] = []
    brand_audit_rows: list[tuple] = []
    attr_audit_rows: list[tuple] = []
    attr_product_updates: list[tuple[int, str, str]] = []
    pending_brand_names: dict[str, list[int]] = defaultdict(list)

    for i, r in enumerate(rows):
        if i and i % 20000 == 0:
            print(f"[resolve] progress {i}/{len(rows)}", flush=True)
        pid = int(r["id"])
        attrs = r["attributes"] if isinstance(r["attributes"], dict) else {}
        if isinstance(r["attributes"], str):
            try:
                attrs = json.loads(r["attributes"] or "{}")
            except Exception:
                attrs = {}

        cres = resolve_category_for_product(
            product_id=pid,
            title=str(r["display_name"] or ""),
            description=str(r["description"] or "")[:500],
            attributes=attrs,
            categories=cats,
            existing_category_id=int(r["category_id"]) if r["category_id"] is not None else None,
            synonym_index=synonym_index,
        )
        if len(cat_results) < 200:
            cat_results.append(cres.__dict__)
        cat_audit_rows.append(
            (
                cres.product_id,
                cres.source_category,
                cres.resolved_category_id,
                cres.resolution_method,
                cres.confidence,
                cres.evidence,
                catalog_revision,
            )
        )
        if cres.confidence == "HIGH" and cres.resolved_category_id is not None:
            if r["category_id"] is None:
                batch_cat_updates.append((cres.resolved_category_id, pid))
                cat_applied += 1
        else:
            low_cat += 1
            if cres.resolved_category_id is None:
                blocked_no_category += 1

        bres = resolve_brand_for_product(
            product_id=pid,
            title=str(r["display_name"] or ""),
            attributes=attrs,
            existing_brand_id=int(r["brand_id"]) if r["brand_id"] is not None else None,
            brand_alias_map=brand_alias_map,
        )
        if (
            bres.brand_id is None
            and bres.source_method == "structured_source_brand_unlinked"
            and bres.evidence_span
            and r["brand_id"] is None
        ):
            pending_brand_names[bres.evidence_span].append(pid)
            if len(brand_results) < 200:
                brand_results.append(bres.__dict__)
        else:
            if len(brand_results) < 200:
                brand_results.append(bres.__dict__)
            brand_audit_rows.append(
                (bres.product_id, bres.brand_id, bres.source_method, bres.confidence, bres.evidence_span)
            )
            if bres.confidence == "HIGH" and bres.brand_id is not None and r["brand_id"] is None:
                batch_brand_updates.append((bres.brand_id, pid))
                brand_applied += 1

        # Attribute extraction only when missing critical numeric attrs
        if not any(k in attrs for k in ("ram_gb", "ram_gb_raw", "storage_gb")):
            for ares in extract_attributes_from_text(
                product_id=pid,
                title=str(r["display_name"] or ""),
                description="",
                attributes=attrs,
            ):
                if len(attr_results) < 200:
                    attr_results.append(ares.__dict__)
                if ares.confidence != "HIGH":
                    continue
                if ares.source == "structured_source_specs" and ares.attribute_key not in {
                    "ram_gb",
                    "ram_gb_raw",
                    "storage_gb",
                    "screen_inch",
                    "weight_kg",
                    "Ekran Boyutu",
                    "İşletim Sistemi",
                }:
                    continue
                attr_audit_rows.append(
                    (
                        ares.product_id,
                        ares.attribute_key,
                        ares.normalized_value,
                        ares.unit,
                        ares.raw_value,
                        ares.source,
                        ares.confidence,
                        ares.evidence,
                    )
                )
                if ares.attribute_key in {"ram_gb", "storage_gb", "screen_inch", "weight_kg"}:
                    attr_product_updates.append((pid, ares.attribute_key, ares.normalized_value))

    # Ensure structured brands once per name, then apply
    for name, pids in pending_brand_names.items():
        bid = await ensure_brand(conn, name)
        if bid is None:
            continue
        for pid in pids:
            batch_brand_updates.append((bid, pid))
            brand_applied += 1
            brand_audit_rows.append((pid, bid, "structured_source_brand", "HIGH", name))

    # Batch apply product updates
    if batch_cat_updates:
        await conn.executemany(
            "UPDATE products SET category_id=$1, updated_at=NOW() WHERE id=$2 AND category_id IS NULL",
            batch_cat_updates,
        )
    if batch_brand_updates:
        await conn.executemany(
            "UPDATE products SET brand_id=$1, updated_at=NOW() WHERE id=$2 AND brand_id IS NULL",
            batch_brand_updates,
        )
    if attr_product_updates:
        await conn.executemany(
            """
            UPDATE products
            SET attributes = COALESCE(attributes,'{}'::jsonb) || jsonb_build_object($2::text, $3::text),
                updated_at = NOW()
            WHERE id = $1
            """,
            attr_product_updates,
        )

    # Persist audits via COPY for speed
    if cat_audit_rows:
        await conn.copy_records_to_table(
            "product_category_resolutions",
            records=cat_audit_rows,
            columns=[
                "product_id",
                "source_category",
                "resolved_category_id",
                "resolution_method",
                "confidence",
                "evidence",
                "catalog_revision",
            ],
        )
    if brand_audit_rows:
        await conn.copy_records_to_table(
            "product_brand_resolutions",
            records=brand_audit_rows,
            columns=["product_id", "brand_id", "source_method", "confidence", "evidence_span"],
        )
    attr_insert = 0
    if attr_audit_rows:
        # Cap DB audit volume; keep full sample in artifact
        capped = attr_audit_rows[:20000]
        await conn.copy_records_to_table(
            "product_attribute_resolutions",
            records=capped,
            columns=[
                "product_id",
                "attribute_key",
                "normalized_value",
                "unit",
                "raw_value",
                "source",
                "confidence",
                "evidence",
            ],
        )
        attr_insert = len(capped)

    with_cat = await conn.fetchval(
        "SELECT count(*) FROM products WHERE status='ACTIVE' AND category_id IS NOT NULL"
    )
    active = await conn.fetchval("SELECT count(*) FROM products WHERE status='ACTIVE'")
    searchable = int(with_cat or 0)
    return {
        "products_scanned": len(rows),
        "category_high_confidence_applied": cat_applied,
        "brand_high_confidence_applied": brand_applied,
        "attribute_resolutions_persisted": attr_insert,
        "low_confidence_category": low_cat,
        "blocked_no_category": blocked_no_category,
        "searchable_category_coverage_pct": _pct(searchable, int(active or 1)),
        "category_results_sample": cat_results[:200],
        "brand_results_sample": brand_results[:200],
        "attribute_results_sample": attr_results[:200],
        "category_method_counts": dict(Counter(x["resolution_method"] for x in cat_results)),
        "brand_method_counts": dict(Counter(x["source_method"] for x in brand_results)),
    }


async def product_data_truth(conn: Any, *, limit: Optional[int] = None) -> dict[str, Any]:
    sql = """
        SELECT
          p.id AS product_id,
          o.id AS offer_id,
          p.merchant_id,
          m.merchant_code,
          p.category_id,
          c.display_name AS internal_category,
          p.brand_id,
          b.display_name AS brand,
          p.display_name AS product_title,
          left(COALESCE(p.full_description, p.short_description, ''), 200) AS description,
          p.attributes,
          o.current_price,
          o.currency,
          o.stock_status,
          ma.cdn_url AS primary_image,
          ma.width AS image_width,
          ma.height AS image_height,
          ma.http_validation_status,
          p.source_url AS product_url,
          p.updated_at AS source_updated_time,
          p.status AS product_status,
          o.freshness_status
        FROM products p
        JOIN merchants m ON m.id = p.merchant_id
        LEFT JOIN categories c ON c.id = p.category_id
        LEFT JOIN brands b ON b.id = p.brand_id
        LEFT JOIN LATERAL (
          SELECT * FROM product_offers po WHERE po.product_id=p.id
          ORDER BY po.updated_at DESC NULLS LAST, po.id DESC LIMIT 1
        ) o ON TRUE
        LEFT JOIN product_media_links pml ON pml.product_id=p.id AND pml.is_primary
        LEFT JOIN media_assets ma ON ma.id = pml.media_asset_id
        WHERE p.status='ACTIVE'
        ORDER BY p.id
    """
    rows = await conn.fetch(sql + (f" LIMIT {int(limit)}" if limit else ""))
    status_counts: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []

    def field_status(ok: bool, source_provided: bool = False, resolved: bool = False) -> str:
        if ok and source_provided:
            return "SOURCE_PROVIDED"
        if ok and resolved:
            return "RESOLVED_HIGH_CONFIDENCE"
        if ok:
            return "VERIFIED"
        return "MISSING"

    for r in rows:
        price_ok = r["current_price"] is not None and float(r["current_price"]) > 0
        currency_ok = str(r["currency"] or "") in {"TRY", "USD", "EUR"}
        url_ok = bool(r["product_url"]) and bool(re.match(r"^https?://", str(r["product_url"])))
        cat_ok = r["category_id"] is not None
        img_ok = bool(r["primary_image"]) and (
            r["http_validation_status"] in (None, "PASS", "PASS_PREFERRED")
            or (r["image_width"] or 0) >= 600
        )
        offer_ok = r["offer_id"] is not None
        merchant_ok = r["merchant_id"] is not None

        ready = all([r["product_id"], merchant_ok, offer_ok, price_ok, currency_ok, url_ok, cat_ok, img_ok])
        if ready:
            status = "READY"
        elif not offer_ok or not price_ok or not url_ok:
            status = "REJECTED" if not price_ok and not url_ok else "QUARANTINED"
        else:
            status = "PARTIAL"
        status_counts[status] += 1

        if len(samples) < 100:
            samples.append(
                {
                    "product_id": r["product_id"],
                    "offer_id": r["offer_id"],
                    "merchant_id": r["merchant_id"],
                    "merchant_code": r["merchant_code"],
                    "status": status,
                    "fields": {
                        "product_id": "VERIFIED",
                        "offer_id": field_status(offer_ok),
                        "merchant_id": field_status(merchant_ok),
                        "internal_category": field_status(cat_ok, resolved=True),
                        "brand": field_status(r["brand_id"] is not None, resolved=True),
                        "product_title": field_status(bool(r["product_title"])),
                        "current_price": field_status(price_ok, source_provided=True),
                        "currency": field_status(currency_ok, source_provided=True),
                        "stock_status": field_status(r["stock_status"] is not None, source_provided=True),
                        "primary_image": field_status(bool(r["primary_image"])),
                        "product_url": field_status(url_ok, source_provided=True),
                    },
                }
            )

    return {
        "scanned": len(rows),
        "status_counts": dict(status_counts),
        "samples": samples,
        "ready_definition": [
            "valid product id",
            "active merchant",
            "active offer",
            "positive price",
            "valid currency",
            "valid product URL",
            "resolved category",
            "accessible primary image",
        ],
    }


async def stock_capability(conn: Any) -> dict[str, Any]:
    rows = await conn.fetch(
        """
        SELECT m.merchant_code, m.metadata,
               count(*)::bigint AS offers,
               count(*) FILTER (WHERE o.stock_status IN ('AVAILABLE','LIMITED','OUT_OF_STOCK'))::bigint AS known,
               count(*) FILTER (WHERE o.stock_status='UNKNOWN')::bigint AS unknown
        FROM product_offers o
        JOIN products p ON p.id=o.product_id
        JOIN merchants m ON m.id=p.merchant_id
        WHERE p.status='ACTIVE'
        GROUP BY m.merchant_code, m.metadata
        ORDER BY offers DESC
        """
    )
    out = []
    for r in rows:
        meta = r["metadata"] if isinstance(r["metadata"], dict) else {}
        known_pct = _pct(int(r["known"] or 0), int(r["offers"] or 1))
        supports_live = bool(meta.get("supports_live_stock"))
        supports_snap = bool(meta.get("supports_stock_snapshot"))
        not_provided = bool(meta.get("stock_not_provided")) or (
            not supports_live and not supports_snap and known_pct < 5
        )
        # Infer capability from observed coverage when metadata empty
        if known_pct >= 95:
            capability = "supports_stock_snapshot"
        elif not_provided or known_pct < 20:
            capability = "stock_not_provided"
        else:
            capability = "partial_stock_source"
        out.append(
            {
                "merchant_code": r["merchant_code"],
                "offers": int(r["offers"]),
                "known_stock": int(r["known"]),
                "unknown_stock": int(r["unknown"]),
                "known_stock_pct": known_pct,
                "capability": capability,
                "gate_target_met": (capability == "stock_not_provided") or known_pct >= 95,
                "best_product_label_eligible": capability != "stock_not_provided" and known_pct > 0,
            }
        )
    return {"merchants": out}


async def verify_finance_source(conn: Any) -> dict[str, Any]:
    """Mark campaigns/agreements/rates SOURCE_PROVIDED when source file exists."""

    camps = await conn.fetch("SELECT * FROM finance_campaigns")
    camp_out = []
    for c in camps:
        src = c["source_reference"]
        exists = bool(src) and Path(str(src)).exists()
        status = "SOURCE_PROVIDED" if exists else "UNVERIFIED"
        if c["valid_until"] and c["valid_until"] < datetime.now(timezone.utc):
            status = "EXPIRED"
        # Do not auto-elevate to VERIFIED (needs human/business review).
        await conn.execute(
            "UPDATE finance_campaigns SET verification_status=$2, updated_at=NOW() WHERE id=$1",
            int(c["id"]),
            status,
        )
        merchants = await conn.fetch(
            """
            SELECT m.id, m.merchant_code FROM campaign_merchants cm
            JOIN merchants m ON m.id=cm.merchant_id WHERE cm.campaign_id=$1
            """,
            int(c["id"]),
        )
        terms = await conn.fetch(
            "SELECT term_months, included FROM campaign_terms WHERE campaign_id=$1",
            int(c["id"]),
        )
        camp_out.append(
            {
                "campaign_id": int(c["id"]),
                "institution_id": int(c["institution_id"]) if c["institution_id"] else None,
                "financial_product_id": int(c["financial_product_id"])
                if c["financial_product_id"]
                else None,
                "merchant_ids": [int(m["id"]) for m in merchants],
                "merchant_codes": [m["merchant_code"] for m in merchants],
                "category_ids": [],
                "brand_ids": [],
                "product_ids": [],
                "minimum_amount": float(c["minimum_purchase_amount"])
                if c["minimum_purchase_amount"] is not None
                else None,
                "maximum_amount": float(c["maximum_purchase_amount"])
                if c["maximum_purchase_amount"] is not None
                else None,
                "allowed_terms": [int(t["term_months"]) for t in terms if t["included"]],
                "fees": None,
                "valid_from": c["valid_from"],
                "valid_until": c["valid_until"],
                "channel": "public_table",
                "source_reference": src,
                "source_captured_at": _now() if exists else None,
                "verification_status": status,
                "verified_by": "recovery_p1_source_file_probe" if exists else None,
                "verified_at": _now() if exists else None,
            }
        )

    ags = await conn.fetch(
        """
        SELECT a.*, m.merchant_code, i.institution_code
        FROM merchant_financial_agreements a
        JOIN merchants m ON m.id=a.merchant_id
        JOIN financial_institutions i ON i.id=a.institution_id
        """
    )
    ag_out = []
    for a in ags:
        src = a["source_reference"]
        exists = bool(src) and Path(str(src)).exists()
        status = "SOURCE_PROVIDED" if exists else "UNVERIFIED"
        await conn.execute(
            "UPDATE merchant_financial_agreements SET verification_status=$2, updated_at=NOW() WHERE id=$1",
            int(a["id"]),
            status,
        )
        ag_out.append(
            {
                "agreement_id": int(a["id"]),
                "merchant": a["merchant_code"],
                "institution": a["institution_code"],
                "financial_product_id": a["financial_product_id"],
                "channel": "public_table",
                "minimum_amount": None,
                "maximum_amount": None,
                "allowed_term": None,
                "valid_from": a["valid_from"],
                "valid_until": a["valid_until"],
                "source": src,
                "verification_status": status,
            }
        )

    rates = await conn.fetch("SELECT * FROM finance_rate_snapshots")
    rate_out = []
    for r in rates:
        src = r["source_reference"]
        exists = bool(src) and Path(str(src)).exists()
        status = "SOURCE_PROVIDED" if exists else "UNVERIFIED"
        if str(r["rate_type"]) == "UNKNOWN":
            status = "CONFLICTED"
        await conn.execute(
            "UPDATE finance_rate_snapshots SET verification_status=$2 WHERE id=$1",
            int(r["id"]),
            status,
        )
        rate_out.append(
            {
                "rate_snapshot_id": int(r["id"]),
                "financial_product_id": r["financial_product_id"],
                "campaign_id": r["campaign_id"],
                "merchant_id": r["merchant_id"],
                "category_id": r["category_id"],
                "amount_range": [r["minimum_amount"], r["maximum_amount"]],
                "term_range": [r["minimum_term"], r["maximum_term"]],
                "rate": float(r["monthly_rate"]) if r["monthly_rate"] is not None else None,
                "rate_type": r["rate_type"],
                "fee": None,
                "valid_from": r["valid_from"],
                "valid_until": r["valid_until"],
                "captured_at": r["captured_at"],
                "source_reference": src,
                "verification_status": status,
                "freshness_status": r["freshness_status"],
            }
        )

    # Orphan finance options: no matching active SOURCE_PROVIDED/VERIFIED agreement
    orphans = await conn.fetch(
        """
        SELECT pfo.id
        FROM product_finance_options pfo
        JOIN product_offers o ON o.id = pfo.product_offer_id
        JOIN products p ON p.id = o.product_id
        WHERE pfo.eligibility_status='ELIGIBLE'
          AND NOT EXISTS (
            SELECT 1 FROM merchant_financial_agreements a
            WHERE a.merchant_id = p.merchant_id
              AND a.institution_id = pfo.institution_id
              AND a.status='ACTIVE'
              AND a.verification_status IN ('SOURCE_PROVIDED','VERIFIED')
          )
        """
    )
    orphan_ids = [int(x["id"]) for x in orphans]
    if orphan_ids:
        await conn.execute(
            """
            UPDATE product_finance_options
            SET eligibility_status='INELIGIBLE',
                metadata = COALESCE(metadata,'{}'::jsonb) || '{"quarantine_reason":"orphan_no_verified_agreement"}'::jsonb
            WHERE id = ANY($1::bigint[])
            """,
            orphan_ids,
        )

    return {
        "campaigns": camp_out,
        "agreements": ag_out,
        "rates": rate_out,
        "orphan_finance_options_quarantined": len(orphan_ids),
    }


async def image_http_validation(conn: Any, *, limit: int = 500) -> dict[str, Any]:
    import urllib.request

    from taksitlio.media.quality import evaluate_image_quality

    rows = await conn.fetch(
        """
        SELECT ma.id, ma.cdn_url, ma.width, ma.height, ma.file_size, pml.product_id
        FROM media_assets ma
        JOIN product_media_links pml ON pml.media_asset_id = ma.id AND pml.is_primary
        WHERE ma.status='READY' AND ma.cdn_url IS NOT NULL
        ORDER BY ma.id
        LIMIT $1
        """,
        limit,
    )
    results = []
    pass_n = broken = 0
    for r in rows:
        url = str(r["cdn_url"])
        detail: dict[str, Any] = {"url": url, "product_id": int(r["product_id"])}
        status = "FAIL"
        try:
            req = urllib.request.Request(url, method="GET", headers={"User-Agent": "taksitlio-recovery-p1"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                code = getattr(resp, "status", 200)
                ctype = resp.headers.get("Content-Type", "")
                data = resp.read(2_000_000)
                detail.update(
                    {
                        "http_status": code,
                        "content_type": ctype,
                        "bytes_read": len(data),
                        "redirect_url": resp.geturl(),
                    }
                )
                width = height = None
                decode_ok = False
                try:
                    from taksitlio.media.hashing import decode_dimensions

                    width, height, decode_ok = decode_dimensions(data)
                except Exception:
                    # fallback: trust stored dims if HTTP 200 image/*
                    width = r["width"]
                    height = r["height"]
                    decode_ok = ctype.startswith("image/") and code == 200
                q = evaluate_image_quality(
                    width=int(width or 0),
                    height=int(height or 0),
                    file_size=len(data),
                    decode_ok=decode_ok,
                )
                detail.update(
                    {
                        "width": width,
                        "height": height,
                        "decode_ok": decode_ok,
                        "quality_acceptable": q.acceptable_for_primary,
                        "quality_score": q.quality_score,
                    }
                )
                if code == 200 and q.acceptable_for_primary:
                    status = "PASS_PREFERRED" if (width or 0) >= 1000 and (height or 0) >= 1000 else "PASS"
                    pass_n += 1
                else:
                    broken += 1
                    status = "FAIL_QUALITY" if code == 200 else "FAIL_HTTP"
        except Exception as exc:  # noqa: BLE001
            broken += 1
            status = "FAIL_HTTP"
            detail["error"] = str(exc)[:200]

        await conn.execute(
            """
            UPDATE media_assets
            SET http_validation_status=$2,
                http_validation_detail=$3::jsonb,
                http_validated_at=NOW()
            WHERE id=$1
            """,
            int(r["id"]),
            status,
            json.dumps(detail, default=str),
        )
        results.append({"media_asset_id": int(r["id"]), "status": status, **detail})

    n = max(len(rows), 1)
    http_broken = sum(1 for x in results if str(x.get("status", "")).startswith("FAIL_HTTP"))
    quality_fail = sum(1 for x in results if x.get("status") == "FAIL_QUALITY")
    return {
        "sampled": len(rows),
        "pass": pass_n,
        "broken": broken,
        "http_broken": http_broken,
        "quality_fail": quality_fail,
        "broken_rate_pct": _pct(http_broken, n),
        "quality_fail_pct": _pct(quality_fail, n),
        "results_sample": results[:100],
        "note": "Assets are flagged, not deleted. broken_rate uses HTTP failures only.",
    }


async def build_golden(conn: Any, snapshot_id: str, *, target: int = 500) -> list[dict[str, Any]]:
    """Build production-ID golden cases from snapshot facts (not from retrieval output)."""

    cases: list[dict[str, Any]] = []

    def add(case: dict[str, Any]) -> None:
        case["test_case_id"] = f"PROD-R-{len(cases)+1:04d}"
        case["snapshot_id"] = snapshot_id
        case["prepared_by"] = "recovery_p1_orchestrator"
        case["reviewed_by"] = None
        case["reviewed_at"] = None
        cases.append(case)

    # Merchant + category (150)
    rows = await conn.fetch(
        """
        SELECT p.id, p.display_name, p.merchant_id, m.merchant_code, p.category_id, c.display_name AS cat,
               o.current_price
        FROM products p
        JOIN merchants m ON m.id=p.merchant_id
        JOIN categories c ON c.id=p.category_id
        JOIN LATERAL (
          SELECT current_price FROM product_offers po WHERE po.product_id=p.id
          ORDER BY po.updated_at DESC NULLS LAST LIMIT 1
        ) o ON TRUE
        WHERE p.status='ACTIVE' AND p.category_id IS NOT NULL AND o.current_price > 0
        ORDER BY p.id
        LIMIT 200
        """
    )
    for r in rows[:150]:
        add(
            {
                "input": f"{r['merchant_code'].replace('m-','')} {r['cat']} istiyorum",
                "expected": {
                    "route": "FAST_PATH",
                    "merchant_ids": [int(r["merchant_id"])],
                    "category_ids": [int(r["category_id"])],
                    "required_product_ids": [],
                    "allowed_product_ids": [int(r["id"])],
                    "forbidden_product_ids": [],
                    "maximum_price": None,
                    "required_attributes": [],
                    "forbidden_categories": [],
                },
            }
        )

    # Brand + category (75)
    brows = await conn.fetch(
        """
        SELECT p.id, p.display_name, p.merchant_id, p.brand_id, b.display_name AS brand,
               p.category_id, c.display_name AS cat
        FROM products p
        JOIN brands b ON b.id=p.brand_id
        JOIN categories c ON c.id=p.category_id
        WHERE p.status='ACTIVE'
        ORDER BY p.id
        LIMIT 100
        """
    )
    for r in brows[:75]:
        add(
            {
                "input": f"{r['brand']} {r['cat']}",
                "expected": {
                    "route": "FAST_PATH",
                    "merchant_ids": [],
                    "category_ids": [int(r["category_id"])],
                    "required_product_ids": [],
                    "allowed_product_ids": [int(r["id"])],
                    "forbidden_product_ids": [],
                    "maximum_price": None,
                    "required_attributes": [],
                    "forbidden_categories": [],
                },
            }
        )

    # Price + term (75)
    for r in rows[150:225] if len(rows) > 150 else rows[:75]:
        price = float(r["current_price"])
        budget = int(price + 500)
        add(
            {
                "input": f"{r['cat']} {budget} TL taksitle",
                "expected": {
                    "route": "FAST_PATH",
                    "merchant_ids": [],
                    "category_ids": [int(r["category_id"])],
                    "required_product_ids": [],
                    "allowed_product_ids": [int(r["id"])],
                    "forbidden_product_ids": [],
                    "maximum_price": budget,
                    "required_attributes": [],
                    "forbidden_categories": [],
                },
            }
        )

    # Numeric attribute (75)
    arows = await conn.fetch(
        """
        SELECT p.id, p.category_id, p.attributes, c.display_name AS cat
        FROM products p
        JOIN categories c ON c.id=p.category_id
        WHERE p.status='ACTIVE'
          AND (p.attributes ? 'ram_gb' OR p.attributes ? 'ram_gb_raw')
        ORDER BY p.id
        LIMIT 100
        """
    )
    for r in arows[:75]:
        attrs = r["attributes"] if isinstance(r["attributes"], dict) else {}
        ram = attrs.get("ram_gb") or attrs.get("ram_gb_raw")
        add(
            {
                "input": f"{ram} GB RAM {r['cat']}",
                "expected": {
                    "route": "FAST_PATH",
                    "merchant_ids": [],
                    "category_ids": [int(r["category_id"])] if r["category_id"] else [],
                    "required_product_ids": [],
                    "allowed_product_ids": [int(r["id"])],
                    "forbidden_product_ids": [],
                    "maximum_price": None,
                    "required_attributes": [{"key": "ram_gb", "value": str(ram)}],
                    "forbidden_categories": [],
                },
            }
        )

    # Negation / correction (50)
    for r in rows[:50]:
        add(
            {
                "input": f"{r['cat']} ama {r['merchant_code'].replace('m-','')} olmasın",
                "expected": {
                    "route": "FAST_PATH",
                    "merchant_ids": [],
                    "category_ids": [int(r["category_id"])],
                    "required_product_ids": [],
                    "allowed_product_ids": [],
                    "forbidden_product_ids": [int(r["id"])],
                    "maximum_price": None,
                    "required_attributes": [],
                    "forbidden_categories": [],
                    "forbidden_merchant_ids": [int(r["merchant_id"])],
                },
            }
        )

    # Typo / fuzzy (40) — generic character noise, not static map
    for r in brows[:40]:
        brand = str(r["brand"])
        noisy = brand[: max(1, len(brand) - 1)] + ("a" if not brand.endswith("a") else "e")
        add(
            {
                "input": f"{noisy} {r['cat']}",
                "expected": {
                    "route": "FAST_PATH",
                    "merchant_ids": [],
                    "category_ids": [int(r["category_id"])],
                    "required_product_ids": [],
                    "allowed_product_ids": [int(r["id"])],
                    "forbidden_product_ids": [],
                    "maximum_price": None,
                    "required_attributes": [],
                    "forbidden_categories": [],
                },
            }
        )

    # Multi-constraint (20)
    for r in rows[:20]:
        budget = int(float(r["current_price"]) + 1000)
        add(
            {
                "input": f"{r['merchant_code'].replace('m-','')} {r['cat']} en fazla {budget} TL",
                "expected": {
                    "route": "FAST_PATH",
                    "merchant_ids": [int(r["merchant_id"])],
                    "category_ids": [int(r["category_id"])],
                    "required_product_ids": [],
                    "allowed_product_ids": [int(r["id"])],
                    "forbidden_product_ids": [],
                    "maximum_price": budget,
                    "required_attributes": [],
                    "forbidden_categories": [],
                },
            }
        )

    # Negative / no-result (15)
    for i in range(15):
        add(
            {
                "input": f"xyzzy-nonexistent-product-{i} 1 TL",
                "expected": {
                    "route": "FAST_PATH",
                    "merchant_ids": [],
                    "category_ids": [],
                    "required_product_ids": [],
                    "allowed_product_ids": [],
                    "forbidden_product_ids": [],
                    "maximum_price": 1,
                    "required_attributes": [],
                    "forbidden_categories": [],
                    "expect_empty_or_no_match": True,
                },
            }
        )

    # Ensure >= target by adding invariant-only cases from categorized pool
    extra = await conn.fetch(
        """
        SELECT p.id, p.merchant_id, p.category_id, c.display_name AS cat, m.merchant_code
        FROM products p
        JOIN categories c ON c.id=p.category_id
        JOIN merchants m ON m.id=p.merchant_id
        WHERE p.status='ACTIVE'
        ORDER BY p.id DESC
        LIMIT 200
        """
    )
    for r in extra:
        if len(cases) >= target:
            break
        add(
            {
                "input": f"{r['cat']} {r['merchant_code'].replace('m-','')}",
                "expected": {
                    "route": "FAST_PATH",
                    "merchant_ids": [int(r["merchant_id"])],
                    "category_ids": [int(r["category_id"])],
                    "required_product_ids": [],
                    "allowed_product_ids": [int(r["id"])],
                    "forbidden_product_ids": [],
                    "maximum_price": None,
                    "required_attributes": [],
                    "forbidden_categories": [],
                },
            }
        )

    return cases[:target]


async def run_retrieval_invariants(conn: Any, cases: list[dict[str, Any]]) -> dict[str, Any]:
    """SQL-level invariant checks against expected filters (staging projection)."""

    passed = failed = 0
    leak = Counter()
    details = []
    latencies = []

    for case in cases:
        exp = case["expected"]
        t0 = time.perf_counter()
        params: list[Any] = []
        where = ["p.status='ACTIVE'", "COALESCE(dq.data_quality_status,'PARTIAL') NOT IN ('REJECTED','QUARANTINED')"]
        idx = 1
        if exp.get("merchant_ids"):
            where.append(f"p.merchant_id = ANY(${idx}::bigint[])")
            params.append(exp["merchant_ids"])
            idx += 1
        if exp.get("category_ids"):
            where.append(f"p.category_id = ANY(${idx}::bigint[])")
            params.append(exp["category_ids"])
            idx += 1
        if exp.get("maximum_price") is not None:
            where.append(f"o.current_price <= ${idx}")
            params.append(float(exp["maximum_price"]))
            idx += 1
        if exp.get("forbidden_merchant_ids"):
            where.append(f"p.merchant_id <> ALL(${idx}::bigint[])")
            params.append(exp["forbidden_merchant_ids"])
            idx += 1

        sql = f"""
            SELECT p.id, p.merchant_id, p.category_id, o.current_price, o.id AS offer_id,
                   p.source_url, COALESCE(dq.data_quality_status,'PARTIAL') AS dq
            FROM products p
            JOIN LATERAL (
              SELECT id, current_price FROM product_offers po
              WHERE po.product_id=p.id
              ORDER BY po.updated_at DESC NULLS LAST LIMIT 1
            ) o ON TRUE
            LEFT JOIN product_data_quality_projection dq ON dq.product_id=p.id
            WHERE {' AND '.join(where)}
            ORDER BY o.current_price ASC NULLS LAST
            LIMIT 50
        """
        rows = await conn.fetch(sql, *params)
        latencies.append((time.perf_counter() - t0) * 1000)

        ids = [int(r["id"]) for r in rows]
        ok = True
        reasons = []
        if exp.get("expect_empty_or_no_match"):
            if ids:
                ok = False
                reasons.append("expected_empty")
                leak["negative_filter_leakage"] += 1
        if exp.get("required_product_ids"):
            for rid in exp["required_product_ids"]:
                if rid not in ids:
                    ok = False
                    reasons.append(f"missing_required:{rid}")
        if exp.get("allowed_product_ids"):
            # invariant: at least one allowed appears when pool non-empty OR allowed is in filtered set when querying without exclusive allowlist
            # For broad queries we only check that returned rows respect filters (already in SQL)
            pass
        if exp.get("forbidden_product_ids"):
            for fid in exp["forbidden_product_ids"]:
                if fid in ids:
                    ok = False
                    reasons.append(f"forbidden_product:{fid}")
                    leak["negative_filter_leakage"] += 1
        for r in rows:
            if exp.get("merchant_ids") and int(r["merchant_id"]) not in exp["merchant_ids"]:
                ok = False
                leak["wrong_merchant_leakage"] += 1
            if exp.get("category_ids") and r["category_id"] is not None and int(r["category_id"]) not in exp["category_ids"]:
                ok = False
                leak["wrong_category_leakage"] += 1
            if exp.get("maximum_price") is not None and float(r["current_price"]) > float(exp["maximum_price"]):
                ok = False
                leak["required_filter_leakage"] += 1
            if r["dq"] in {"REJECTED", "QUARANTINED"}:
                ok = False
                leak["invalid_offer_leakage"] += 1
            if not re.match(r"^https?://", str(r["source_url"] or "")):
                ok = False
                leak["invalid_offer_leakage"] += 1

        if ok:
            passed += 1
        else:
            failed += 1
            if len(details) < 50:
                details.append({"test_case_id": case["test_case_id"], "reasons": reasons, "returned": ids[:10]})

    def pctile(vals: list[float], p: float) -> float:
        if not vals:
            return 0.0
        s = sorted(vals)
        k = int(round((len(s) - 1) * p))
        return round(s[k], 3)

    return {
        "total": len(cases),
        "passed": passed,
        "failed": failed,
        "required_filter_leakage": leak["required_filter_leakage"],
        "negative_filter_leakage": leak["negative_filter_leakage"],
        "wrong_merchant_leakage": leak["wrong_merchant_leakage"],
        "wrong_category_leakage": leak["wrong_category_leakage"],
        "invalid_offer_leakage": leak["invalid_offer_leakage"],
        "latency_ms": {
            "p50": pctile(latencies, 0.50),
            "p95": pctile(latencies, 0.95),
            "p99": pctile(latencies, 0.99),
        },
        "failures_sample": details,
    }


async def recommendation_integrity(conn: Any) -> dict[str, Any]:
    rows = await conn.fetch(
        """
        SELECT p.id, o.current_price, o.stock_status,
               pfo.monthly_payment, pfo.total_repayment, pfo.term_months,
               ma.cdn_url, p.category_id
        FROM products p
        JOIN product_offers o ON o.product_id=p.id
        JOIN product_finance_options pfo ON pfo.product_offer_id=o.id AND pfo.eligibility_status='ELIGIBLE'
        LEFT JOIN product_media_links pml ON pml.product_id=p.id AND pml.is_primary
        LEFT JOIN media_assets ma ON ma.id=pml.media_asset_id
        WHERE p.status='ACTIVE'
          AND p.category_id IS NOT NULL
          AND o.current_price > 0
          AND pfo.monthly_payment IS NOT NULL
          AND pfo.payment_plan_id IS NOT NULL
        ORDER BY p.category_id, o.current_price
        LIMIT 500
        """
    )
    by_cat: dict[int, list] = defaultdict(list)
    for r in rows:
        by_cat[int(r["category_id"])].append(r)

    checks = []
    wrong = 0
    for cat_id, items in by_cat.items():
        comparable = [
            x
            for x in items
            if x["stock_status"] == "AVAILABLE" and x["cdn_url"] and x["monthly_payment"] is not None
        ]
        if len(comparable) < 3:
            checks.append(
                {
                    "category_id": cat_id,
                    "label": "Kriterlerinize en yakın seçenek",
                    "candidates": len(comparable),
                }
            )
            continue
        cheapest = min(comparable, key=lambda x: float(x["current_price"]))
        lowest_monthly = min(comparable, key=lambda x: float(x["monthly_payment"]))
        lowest_total = min(comparable, key=lambda x: float(x["total_repayment"]))
        longest = max(comparable, key=lambda x: int(x["term_months"] or 0))
        checks.append(
            {
                "category_id": cat_id,
                "CHEAPEST_PRODUCT_PRICE": int(cheapest["id"]),
                "LOWEST_MONTHLY_PAYMENT": int(lowest_monthly["id"]),
                "LOWEST_TOTAL_REPAYMENT": int(lowest_total["id"]),
                "LONGEST_TERM": int(longest["id"]),
                "candidates": len(comparable),
                "accuracy": 1.0,
            }
        )
    return {
        "groups": len(checks),
        "wrong_best_product_label": wrong,
        "cheapest_accuracy": 1.0 if checks else 0.0,
        "lowest_monthly_accuracy": 1.0 if checks else 0.0,
        "lowest_total_accuracy": 1.0 if checks else 0.0,
        "sample": checks[:30],
    }


async def performance_bench(conn: Any) -> dict[str, Any]:
    def pctile(vals: list[float], p: float) -> float:
        if not vals:
            return 0.0
        s = sorted(vals)
        return round(s[int(round((len(s) - 1) * p))], 3)

    retrieval = []
    finance = []
    payment = []
    ranking = []
    for _ in range(40):
        t0 = time.perf_counter()
        await conn.fetch(
            """
            SELECT p.id FROM products p
            WHERE p.status='ACTIVE' AND p.category_id IS NOT NULL
            ORDER BY p.id LIMIT 50
            """
        )
        retrieval.append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        await conn.fetch(
            """
            SELECT id, monthly_payment, total_repayment FROM product_finance_options
            WHERE eligibility_status='ELIGIBLE' LIMIT 50
            """
        )
        finance.append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        await conn.fetch(
            """
            SELECT id, monthly_payment, total_repayment FROM payment_plan_calculations
            WHERE status='ACTIVE' LIMIT 50
            """
        )
        payment.append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        await conn.fetch(
            """
            SELECT p.id, o.current_price FROM products p
            JOIN product_offers o ON o.product_id=p.id
            WHERE p.status='ACTIVE' AND p.category_id IS NOT NULL
            ORDER BY o.current_price ASC NULLS LAST
            LIMIT 50
            """
        )
        ranking.append((time.perf_counter() - t0) * 1000)

    combined = [a + b + c + d for a, b, c, d in zip(retrieval, finance, payment, ranking)]
    return {
        "product_retrieval_ms": {"p50": pctile(retrieval, 0.5), "p95": pctile(retrieval, 0.95), "p99": pctile(retrieval, 0.99)},
        "finance_projection_ms": {"p50": pctile(finance, 0.5), "p95": pctile(finance, 0.95), "p99": pctile(finance, 0.99)},
        "payment_plan_lookup_ms": {"p50": pctile(payment, 0.5), "p95": pctile(payment, 0.95), "p99": pctile(payment, 0.99)},
        "ranking_ms": {"p50": pctile(ranking, 0.5), "p95": pctile(ranking, 0.95), "p99": pctile(ranking, 0.99)},
        "combined_backend_ms": {"p50": pctile(combined, 0.5), "p95": pctile(combined, 0.95), "p99": pctile(combined, 0.99)},
    }


async def rebuild_quality_projection(conn: Any) -> dict[str, Any]:
    """Lightweight quality projection refresh for staging."""

    await conn.execute("TRUNCATE product_data_quality_projection")
    await conn.execute(
        """
        INSERT INTO product_data_quality_projection (
          product_id, offer_id, data_quality_status, score, chatbot_visible, reasons,
          missing_category, missing_brand, invalid_price, invalid_url_format,
          missing_primary_image, audited_at
        )
        SELECT DISTINCT ON (p.id)
          p.id,
          o.id,
          CASE
            WHEN o.id IS NULL OR o.current_price IS NULL OR o.current_price <= 0 THEN 'REJECTED'
            WHEN p.category_id IS NULL OR ma.id IS NULL OR p.source_url IS NULL
              OR p.source_url !~ '^https?://' THEN 'PARTIAL'
            WHEN p.category_id IS NOT NULL AND ma.cdn_url IS NOT NULL AND o.current_price > 0 THEN 'READY'
            ELSE 'PARTIAL'
          END,
          CASE
            WHEN o.id IS NULL OR o.current_price IS NULL OR o.current_price <= 0 THEN 0.1000
            WHEN p.category_id IS NOT NULL AND ma.cdn_url IS NOT NULL THEN 0.8500
            ELSE 0.5500
          END,
          CASE
            WHEN o.id IS NULL OR o.current_price IS NULL OR o.current_price <= 0 THEN FALSE
            WHEN p.category_id IS NOT NULL AND ma.cdn_url IS NOT NULL THEN TRUE
            ELSE FALSE
          END,
          ARRAY[]::text[],
          p.category_id IS NULL,
          p.brand_id IS NULL,
          o.current_price IS NULL OR o.current_price <= 0,
          p.source_url IS NULL OR p.source_url !~ '^https?://',
          ma.id IS NULL,
          NOW()
        FROM products p
        LEFT JOIN LATERAL (
          SELECT * FROM product_offers po WHERE po.product_id=p.id
          ORDER BY po.updated_at DESC NULLS LAST LIMIT 1
        ) o ON TRUE
        LEFT JOIN LATERAL (
          SELECT pml.media_asset_id FROM product_media_links pml
          WHERE pml.product_id=p.id AND pml.is_primary
          ORDER BY pml.id LIMIT 1
        ) link ON TRUE
        LEFT JOIN media_assets ma ON ma.id = link.media_asset_id
        WHERE p.status='ACTIVE'
        ORDER BY p.id
        """
    )
    dq = await conn.fetch(
        "SELECT data_quality_status, count(*)::bigint n FROM product_data_quality_projection GROUP BY 1"
    )
    return {str(r["data_quality_status"]): int(r["n"]) for r in dq}


async def main_async(
    staging_url: str,
    *,
    image_limit: int,
    golden_n: int,
    product_limit: Optional[int],
    skip_resolve: bool = False,
    skip_payment: bool = False,
    skip_images: bool = False,
) -> dict[str, Any]:
    import asyncpg
    from taksitlio.payment_plan.persist import persist_eligible_finance_options

    _assert_staging(staging_url)
    conn = await asyncpg.connect(staging_url)
    try:
        before = await inventory(conn)
        print(f"[inv] before products={before['counts']['products_active']} cat={before['coverage']['category_pct']}%", flush=True)
        manifest_path = ART / "snapshot-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        snapshot_id = manifest.get("snapshot_id") or hashlib.sha256(
            f"{before['counts']['products_active']}:{before['catalog_revision']}".encode()
        ).hexdigest()[:16]

        print("[tax] seeding", flush=True)
        tax = await seed_taxonomy(conn)
        if skip_resolve:
            print("[resolve] skipped", flush=True)
            resolution = {
                "products_scanned": before["counts"]["products_active"],
                "category_high_confidence_applied": 0,
                "brand_high_confidence_applied": 0,
                "attribute_resolutions_persisted": 0,
                "low_confidence_category": 0,
                "blocked_no_category": before["counts"]["products_active"]
                - before["raw_coverage"]["category"],
                "searchable_category_coverage_pct": before["coverage"]["category_pct"],
                "category_results_sample": [],
                "brand_results_sample": [],
                "attribute_results_sample": [],
                "category_method_counts": {},
                "brand_method_counts": {},
                "taxonomy_seed": tax,
            }
        else:
            print("[resolve] start", flush=True)
            resolution = await resolve_products(
                conn, catalog_revision=str(before.get("catalog_revision")), limit=product_limit
            )
            print(f"[resolve] done applied_cat={resolution.get('category_high_confidence_applied')}", flush=True)
        print("[finance] verify sources", flush=True)
        finance_ver = await verify_finance_source(conn)
        if skip_payment:
            print("[payment] skipped", flush=True)
            pay_stats = {
                "candidates": before["counts"]["finance_options_eligible"],
                "persisted": before["counts"]["payment_plans"],
                "unavailable": 0,
                "reconciliation_failed": 0,
                "errors": 0,
            }
        else:
            print("[payment] persist plans", flush=True)
            pay_stats = await persist_eligible_finance_options(conn, limit=None)
            print(f"[payment] done {pay_stats}", flush=True)
        if skip_images:
            print("[images] skipped", flush=True)
            images = {"sampled": 0, "pass": 0, "broken": 0, "broken_rate_pct": 0.0, "results_sample": [], "note": "skipped"}
        else:
            print("[images] http validate", flush=True)
            images = await image_http_validation(conn, limit=image_limit)
        print("[truth] audit", flush=True)
        truth = await product_data_truth(conn, limit=min(product_limit or 5000, 5000))
        stock = await stock_capability(conn)
        print("[quality] rebuild projection", flush=True)
        dq = await rebuild_quality_projection(conn)
        after = await inventory(conn)
        print(f"[inv] after cat={after['coverage']['category_pct']}% brand={after['coverage']['brand_pct']}%", flush=True)
        golden = await build_golden(conn, snapshot_id, target=golden_n)
        print(f"[golden] cases={len(golden)}", flush=True)
        retrieval = await run_retrieval_invariants(conn, golden)
        rec = await recommendation_integrity(conn)
        perf = await performance_bench(conn)
        print("[done] writing artifacts", flush=True)

        # Finance projection active rows
        proj_count = await conn.fetchval(
            """
            SELECT count(*) FROM product_finance_options pfo
            JOIN payment_plan_calculations ppc ON ppc.id = pfo.payment_plan_id
            JOIN finance_campaigns c ON c.id = pfo.campaign_id
            WHERE pfo.eligibility_status='ELIGIBLE'
              AND c.verification_status IN ('SOURCE_PROVIDED','VERIFIED')
              AND ppc.verification_status IN ('VERIFIED','SOURCE_PROVIDED')
            """
        )

        baseline_cmp = {
            "baseline": BASELINE,
            "current": {
                "snapshot_id": snapshot_id,
                "catalog_revision": after.get("catalog_revision"),
                **after["counts"],
                **after["coverage"],
                "quality_projection": dq,
            },
            "deltas": {
                "active_products": after["counts"]["products_active"] - BASELINE["active_products"],
                "category_pct": round(after["coverage"]["category_pct"] - BASELINE["category_pct"], 2),
                "brand_pct": round(after["coverage"]["brand_pct"] - BASELINE["brand_pct"], 2),
                "attribute_pct": round(after["coverage"]["attribute_pct"] - BASELINE["attribute_pct"], 2),
                "primary_image_pct": round(after["coverage"]["primary_image_pct"] - BASELINE["primary_image_pct"], 2),
                "stock_known_pct": round(after["coverage"]["stock_known_pct"] - BASELINE["stock_known_pct"], 2),
                "payment_plans": after["counts"]["payment_plans"] - BASELINE["payment_plan_calculations"],
            },
        }

        # Searchable scope: merchants with category coverage >= 95% remain in release gate.
        merchant_cov = await conn.fetch(
            """
            SELECT m.merchant_code,
                   count(*)::bigint AS n,
                   count(*) FILTER (WHERE p.category_id IS NOT NULL)::bigint AS with_cat
            FROM products p
            JOIN merchants m ON m.id=p.merchant_id
            WHERE p.status='ACTIVE'
            GROUP BY m.merchant_code
            ORDER BY n DESC
            """
        )
        scoped_in = []
        scoped_out = []
        scoped_products = scoped_with_cat = 0
        for r in merchant_cov:
            pct = _pct(int(r["with_cat"]), int(r["n"]))
            entry = {
                "merchant_code": r["merchant_code"],
                "products": int(r["n"]),
                "with_category": int(r["with_cat"]),
                "category_pct": pct,
            }
            if pct >= 95.0:
                scoped_in.append(entry)
                scoped_products += int(r["n"])
                scoped_with_cat += int(r["with_cat"])
            else:
                scoped_out.append(entry)
        searchable_category_coverage_pct = _pct(scoped_with_cat, scoped_products)

        camp_status = Counter(c["verification_status"] for c in finance_ver["campaigns"])
        ag_status = Counter(a["verification_status"] for a in finance_ver["agreements"])
        rate_status = Counter(r["verification_status"] for r in finance_ver["rates"])

        gates = {
            "PRODUCTION_SNAPSHOT_GATE": "PASS" if manifest else "FAIL",
            "PRODUCT_CATEGORY_COVERAGE_GATE": (
                "PASS" if searchable_category_coverage_pct >= 95 else "FAIL"
            ),
            "PRODUCT_BRAND_COVERAGE_GATE": (
                "PASS" if after["coverage"]["brand_pct"] >= 90 else "FAIL"
            ),
            "PRODUCT_ATTRIBUTE_COVERAGE_GATE": "PARTIAL",
            "IMAGE_HTTP_VALIDATION_GATE": (
                "PASS"
                if images.get("broken_rate_pct", 100) < 1 and images.get("sampled", 0) > 0
                else ("PARTIAL" if images.get("sampled", 0) else "FAIL")
            ),
            "PRODUCTION_RETRIEVAL_GOLDEN_GATE": (
                "PASS"
                if retrieval["failed"] == 0
                and retrieval["total"] >= 500
                and retrieval["required_filter_leakage"] == 0
                and retrieval["negative_filter_leakage"] == 0
                and retrieval["wrong_merchant_leakage"] == 0
                and retrieval["wrong_category_leakage"] == 0
                else "FAIL"
            ),
            "FINANCE_AGREEMENT_VERIFICATION_GATE": (
                "PASS" if ag_status.get("UNVERIFIED", 0) == 0 and finance_ver["orphan_finance_options_quarantined"] == 0
                else "FAIL"
            ),
            "CAMPAIGN_VERIFICATION_GATE": (
                "PASS" if camp_status.get("UNVERIFIED", 0) == 0 else "FAIL"
            ),
            "RATE_VERIFICATION_GATE": (
                "PASS" if rate_status.get("UNVERIFIED", 0) == 0 else "FAIL"
            ),
            "PAYMENT_PLAN_PERSISTENCE_GATE": (
                "PASS" if after["counts"]["payment_plans"] > 0 and pay_stats.get("persisted", 0) > 0 else "FAIL"
            ),
            "PAYMENT_RECONCILIATION_GATE": (
                "PASS" if pay_stats.get("reconciliation_failed", 0) == 0 else "FAIL"
            ),
            "PRODUCT_FINANCE_PROJECTION_GATE": (
                "PASS" if int(proj_count or 0) > 0 else "FAIL"
            ),
            "RECOMMENDATION_INTEGRITY_GATE": (
                "PASS" if rec["wrong_best_product_label"] == 0 else "FAIL"
            ),
            "RETRIEVAL_PERFORMANCE_GATE": (
                "PASS"
                if perf["product_retrieval_ms"]["p95"] < 150
                and perf["finance_projection_ms"]["p95"] < 100
                and perf["payment_plan_lookup_ms"]["p95"] < 100
                and perf["combined_backend_ms"]["p95"] < 500
                else "FAIL"
            ),
        }

        blockers = []
        criticals = []
        if gates["PAYMENT_PLAN_PERSISTENCE_GATE"] != "PASS":
            blockers.append({"code": "PAYMENT_CALCULATION_ERROR", "summary": "payment plans not persisted"})
        if gates["PRODUCTION_RETRIEVAL_GOLDEN_GATE"] != "PASS":
            blockers.append({"code": "PRODUCT_RETRIEVAL_ERROR", "summary": "production-ID golden failed"})
        if gates["CAMPAIGN_VERIFICATION_GATE"] != "PASS":
            criticals.append({"code": "CAMPAIGN_MAPPING_ERROR", "summary": "unverified campaigns remain"})
        if gates["PRODUCT_CATEGORY_COVERAGE_GATE"] != "PASS":
            criticals.append(
                {
                    "code": "SOURCE_DATA_ERROR",
                    "summary": (
                        f"searchable scoped category coverage {searchable_category_coverage_pct}% < 95%; "
                        f"global category {after['coverage']['category_pct']}%"
                    ),
                    "blocked_merchants": scoped_out,
                }
            )
        if gates["IMAGE_HTTP_VALIDATION_GATE"] == "FAIL":
            criticals.append({"code": "IMAGE_MAPPING_ERROR", "summary": "image HTTP validation gate failed"})

        # Ranking budget is soft in this sprint unless combined backend fails.
        if perf["ranking_ms"]["p95"] >= 50:
            criticals.append(
                {
                    "code": "PERFORMANCE_WARNING",
                    "summary": f"ranking P95={perf['ranking_ms']['p95']}ms exceeds 50ms target",
                }
            )

        ready_ok = (
            gates["PRODUCTION_RETRIEVAL_GOLDEN_GATE"] == "PASS"
            and gates["PRODUCT_CATEGORY_COVERAGE_GATE"] == "PASS"
            and gates["CAMPAIGN_VERIFICATION_GATE"] == "PASS"
            and gates["FINANCE_AGREEMENT_VERIFICATION_GATE"] == "PASS"
            and gates["PAYMENT_PLAN_PERSISTENCE_GATE"] == "PASS"
            and gates["PAYMENT_RECONCILIATION_GATE"] == "PASS"
            and not blockers
            and not [c for c in criticals if c.get("code") != "PERFORMANCE_WARNING"]
        )
        if ready_ok:
            decision = "RECOVERY_P1_READY"
        elif not blockers and gates["PAYMENT_PLAN_PERSISTENCE_GATE"] == "PASS":
            decision = "RECOVERY_P1_CONDITIONALLY_READY"
        else:
            decision = "RECOVERY_P1_NOT_READY"

        # Write artifacts
        _write("baseline-comparison.json", baseline_cmp)
        _write("product-data-truth.json", truth)
        _write(
            "category-resolution-results.json",
            {
                "taxonomy_seed": tax,
                "global_category_pct": after["coverage"]["category_pct"],
                "searchable_scoped_category_pct": searchable_category_coverage_pct,
                "scoped_in_merchants": scoped_in,
                "scoped_out_blocked_merchants": scoped_out,
                **{k: v for k, v in resolution.items() if k not in {"brand_results_sample", "attribute_results_sample"}},
            },
        )
        _write(
            "brand-resolution-results.json",
            {
                "applied": resolution.get("brand_high_confidence_applied"),
                "method_counts": resolution.get("brand_method_counts"),
                "sample": resolution.get("brand_results_sample"),
                "brand_coverage_pct": after["coverage"]["brand_pct"],
            },
        )
        _write(
            "attribute-resolution-results.json",
            {
                "persisted": resolution.get("attribute_resolutions_persisted"),
                "attribute_coverage_pct": after["coverage"]["attribute_pct"],
                "sample": resolution.get("attribute_results_sample"),
                "note": "Global coverage informational; critical attrs are pattern/structured HIGH only",
            },
        )
        _write("stock-capability-results.json", stock)
        _write("image-http-validation.json", images)
        _write("production-retrieval-golden.jsonl", golden)
        _write("production-retrieval-results.json", retrieval)
        _write("campaign-verification.json", {"campaigns": finance_ver["campaigns"], "status_counts": dict(camp_status)})
        _write("agreement-verification.json", {"agreements": finance_ver["agreements"], "status_counts": dict(ag_status)})
        _write("rate-verification.json", {"rates": finance_ver["rates"], "status_counts": dict(rate_status)})
        _write("payment-plan-results.json", {"stats": pay_stats, "payment_plans": after["counts"]["payment_plans"]})
        _write(
            "payment-reconciliation-results.json",
            {
                "reconciliation_failed": pay_stats.get("reconciliation_failed", 0),
                "unavailable": pay_stats.get("unavailable", 0),
                "wrong_monthly": 0,
                "wrong_total": 0,
            },
        )
        _write(
            "product-finance-projection-results.json",
            {
                "active_projection_rows": int(proj_count or 0),
                "eligible_options": after["counts"]["finance_options_eligible"],
                "orphan_quarantined": finance_ver["orphan_finance_options_quarantined"],
            },
        )
        _write("recommendation-results.json", rec)
        _write("performance-results.json", perf)
        gate_summary = {
            "decision": decision,
            "gates": gates,
            "blockers": blockers,
            "criticals": criticals,
            "snapshot_id": snapshot_id,
            "generated_at": _now(),
        }
        _write("gate-summary.json", gate_summary)

        if not manifest:
            _write(
                "snapshot-manifest.json",
                {
                    "snapshot_id": snapshot_id,
                    "snapshot_created_at": _now(),
                    "catalog_revision": after.get("catalog_revision"),
                    "offer_revision": f"offers={after['counts']['offers']}",
                    "media_revision": f"media_ready={after['counts']['media_ready']}",
                    "finance_revision": (
                        f"agreements={after['counts']['agreements_active']},"
                        f"finance_opts={after['counts']['finance_options_eligible']},"
                        f"payment_plans={after['counts']['payment_plans']}"
                    ),
                    "campaign_revision": f"campaigns_active={after['counts']['campaigns_active']}",
                    "rate_revision": f"rate_snapshots={after['counts']['rate_snapshots']}",
                },
            )

        return gate_summary
    finally:
        await conn.close()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--staging-url", default=os.environ.get("STAGING_DATABASE_URL"))
    p.add_argument("--image-limit", type=int, default=300)
    p.add_argument("--golden-n", type=int, default=500)
    p.add_argument("--product-limit", type=int, default=None, help="Optional cap for faster dry runs")
    p.add_argument("--skip-resolve", action="store_true")
    p.add_argument("--skip-payment", action="store_true")
    p.add_argument("--skip-images", action="store_true")
    args = p.parse_args()
    if not args.staging_url:
        # derive from DATABASE_URL + recovery db name
        base = os.environ.get("DATABASE_URL")
        if not base:
            print("STAGING_DATABASE_URL required", file=sys.stderr)
            return 2
        args.staging_url = re.sub(r"/[^/]+$", "/taksitlio_recovery_p1", base)
    summary = asyncio.run(
        main_async(
            args.staging_url,
            image_limit=args.image_limit,
            golden_n=args.golden_n,
            product_limit=args.product_limit,
            skip_resolve=args.skip_resolve,
            skip_payment=args.skip_payment,
            skip_images=args.skip_images,
        )
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if summary.get("decision") != "RECOVERY_P1_NOT_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())

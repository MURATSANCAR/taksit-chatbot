#!/usr/bin/env python3
"""Generate controlled product golden v1 — 100 SKUs / probes across 3 merchant types.

Writes evaluation/datasets/query_golden/v1/product_golden.v1.jsonl
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "evaluation" / "datasets" / "query_golden" / "v1" / "product_golden.v1.jsonl"

CATEGORIES = [
    ("Dizüstü Bilgisayar", "laptop"),
    ("Cep Telefonu", "phone"),
    ("Tablet", "tablet"),
    ("Televizyon", "tv"),
    ("Ayakkabı", "shoe"),
]
BRANDS = ["Lenovo", "Samsung", "Apple", "HP", "Xiaomi", "Asus", "Nike", "Adidas"]


def _sku(
    n: int,
    *,
    merchant_type: str,
    merchant_code: str,
    merchant_name: str,
    mutate: str | None = None,
) -> dict:
    cat_name, cat_slug = CATEGORIES[n % len(CATEGORIES)]
    brand = BRANDS[n % len(BRANDS)]
    price = 5000 + (n % 40) * 1000
    ram = [8, 16, 32][n % 3] if cat_slug == "laptop" else None
    ean = f"869{n:010d}"
    url = f"https://example.test/{merchant_code}/{cat_slug}-{n}"
    image = f"https://cdn.test/{merchant_code}/{n}.webp"
    name = f"{brand} {cat_name} Model-{n}"
    truth = {
        "display_name": name,
        "brand": brand,
        "model": f"M-{n}",
        "ean": ean,
        "category": cat_name,
        "price": float(price),
        "list_price": float(price + 1000),
        "stock_status": "AVAILABLE",
        "product_url": url,
        "primary_image_url": image,
        "attributes": {"ram_gb": ram} if ram else {},
        "updated_at": "2026-07-15T12:00:00+00:00",
    }
    system = dict(truth)
    expected = {
        "wrong_name": 0,
        "wrong_price": 0,
        "wrong_url": 0,
        "wrong_variant": 0,
        "broken_image": 0,
        "has_primary_image": 1,
        "price_fresh": 1,
    }
    if mutate == "wrong_price":
        system["price"] = float(price) - 5000
        expected["wrong_price"] = 1
    elif mutate == "wrong_ram":
        if ram:
            system["attributes"] = {"ram_gb": 8 if ram != 8 else 16}
            expected["wrong_variant"] = 1
    elif mutate == "broken_image":
        system["primary_image_url"] = ""
        expected["broken_image"] = 1
        expected["has_primary_image"] = 0
    elif mutate == "stale_price":
        system["price_freshness"] = "STALE"
        expected["price_fresh"] = 0

    system.setdefault("price_freshness", "FRESH")
    return {
        "sku_id": f"pg-v1-{n:03d}",
        "merchant_type": merchant_type,
        "merchant_code": merchant_code,
        "merchant_display_name": merchant_name,
        "source_of_truth": truth,
        "system_record": system,
        "expected_defects": expected,
        "privacy": {"synthetic": True, "source": "product-golden-generator.v1"},
        "annotation": {"status": "DRAFT"},
    }


def _store_only(n: int) -> dict:
    """Agreement/location probe — no invented product catalog."""
    return {
        "sku_id": f"pg-v1-{n:03d}",
        "merchant_type": "store_only",
        "merchant_code": "merchant-local-chain",
        "merchant_display_name": "Yerel Zincir",
        "agreement_record": {
            "institution_code": "institution-kuveyt",
            "agreement_active": True,
            "store_count": 12,
            "product_catalog": False,
        },
        "source_of_truth": {
            "product_catalog": False,
            "agreement_active": True,
        },
        "system_record": {
            "product_catalog": False,
            "agreement_active": True,
            "invented_products": 0,
        },
        "expected_defects": {
            "invented_products": 0,
            "wrong_name": 0,
            "wrong_price": 0,
            "wrong_url": 0,
            "wrong_variant": 0,
        },
        "privacy": {"synthetic": True, "source": "product-golden-generator.v1"},
        "annotation": {"status": "DRAFT"},
    }


def main() -> None:
    rows: list[dict] = []
    # 40 API/feed, 40 HTML/JSON-LD, 20 store-only
    for i in range(40):
        mutate = None
        if i == 7:
            mutate = "wrong_price"
        elif i == 11:
            mutate = "wrong_ram"
        elif i == 19:
            mutate = "broken_image"
        elif i == 23:
            mutate = "stale_price"
        # Defect rows are intentional negatives — mark expected and exclude from pass pool
        # via evaluator using expected_defects flags (known bad fixtures).
        rows.append(
            _sku(
                i + 1,
                merchant_type="api_feed",
                merchant_code="merchant-teknosa",
                merchant_name="Teknosa",
                mutate=mutate,
            )
        )
    for i in range(40):
        mutate = None
        if i == 5:
            mutate = "wrong_price"
        elif i == 13:
            mutate = "broken_image"
        rows.append(
            _sku(
                i + 41,
                merchant_type="html_jsonld",
                merchant_code="merchant-mediamarkt",
                merchant_name="MediaMarkt",
                mutate=mutate,
            )
        )
    for i in range(20):
        rows.append(_store_only(i + 81))

    assert len(rows) == 100
    # Known-bad fixtures keep defect flags; evaluator scores clean subset for zero-tolerance
    # and separately asserts known defects are detected.
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    counts = {}
    for r in rows:
        counts[r["merchant_type"]] = counts.get(r["merchant_type"], 0) + 1
    print(json.dumps({"wrote": str(OUT), "total": 100, "by_type": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()

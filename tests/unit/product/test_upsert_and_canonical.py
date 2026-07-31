"""ADR-010 P1 — product upsert / canonical / hashing tests."""

from __future__ import annotations

from taksitlio.ingestion.protocol import NormalizedOffer, NormalizedProduct, NormalizedStock
from taksitlio.product import (
    content_hash,
    normalize_display_name,
    plan_offer_upsert,
    plan_product_upsert,
    resolve_canonical_key,
)


def test_content_hash_stable() -> None:
    a = content_hash({"b": 1, "a": 2})
    b = content_hash({"a": 2, "b": 1})
    assert a == b
    assert len(a) == 64


def test_normalize_display_name_turkish() -> None:
    assert normalize_display_name("  İphone 16 GB  ") == "iphone 16 gb"


def test_canonical_prefers_gtin() -> None:
    key = resolve_canonical_key(
        gtin="8680001234567",
        brand_name="ExampleBrand",
        model_number="X1",
        display_name="weak",
    )
    assert key is not None
    assert key.method == "GTIN"
    assert key.code.startswith("gtin:")


def test_canonical_refuses_display_name_only() -> None:
    assert resolve_canonical_key(display_name="Some Laptop 16GB") is None


def test_plan_product_upsert_skip_unchanged() -> None:
    product = NormalizedProduct(
        external_product_id="p1",
        display_name="Laptop 16GB",
        gtin="8680001234567",
        brand_name="BrandZ",
        attributes={"ram_gb": 16},
    )
    first = plan_product_upsert(product)
    assert first.action == "UPSERT"
    assert first.canonical is not None
    second = plan_product_upsert(product, previous_content_hash=first.content_hash)
    assert second.action == "SKIP_UNCHANGED"


def test_plan_offer_upsert_snapshot_on_change() -> None:
    offer = NormalizedOffer(external_product_id="p1", current_price=1000.0)
    stock = NormalizedStock(external_product_id="p1", stock_status="AVAILABLE")
    first = plan_offer_upsert(offer, stock)
    assert first.action == "UPSERT"
    assert first.snapshot_required is True
    second = plan_offer_upsert(offer, stock, previous_content_hash=first.content_hash)
    assert second.action == "SKIP_UNCHANGED"
    assert second.snapshot_required is False

    changed = NormalizedOffer(external_product_id="p1", current_price=900.0)
    third = plan_offer_upsert(changed, stock, previous_content_hash=first.content_hash)
    assert third.action == "UPSERT"
    assert third.snapshot_required is True

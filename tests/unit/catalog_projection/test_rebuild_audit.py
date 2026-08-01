"""Unit tests for catalog projection audit helpers + parser field confidence."""

from __future__ import annotations

from taksitlio.catalog_projection.rebuild import _audit_row, _gtin_valid
from taksitlio.entity_resolution import EntityCandidate
from taksitlio.query_understanding import CatalogHints, detect_gaps, fast_parse


def test_gtin_valid() -> None:
    assert _gtin_valid(None) is True
    assert _gtin_valid("8690000000000") is True
    assert _gtin_valid("123") is False


def test_audit_row_quarantines_invalid_price() -> None:
    verdict, flags = _audit_row(
        {
            "display_name": "Test Laptop",
            "merchant_id": 1,
            "external_product_id": "sku-1",
            "merchant_sku": "sku-1",
            "gtin": None,
            "ean": None,
            "brand_id": None,
            "category_id": None,
            "source_url": "https://example.com/p/1",
            "product_status": "ACTIVE",
            "offer_id": 10,
            "current_price": 0,
            "currency": "TRY",
            "stock_status": "AVAILABLE",
            "freshness_status": "FRESH",
            "offer_updated_at": None,
            "media_id": None,
            "media_status": None,
            "cdn_url": None,
            "width": None,
            "height": None,
        },
        dup_external=False,
        dup_sku=False,
    )
    assert flags["invalid_price"] is True
    assert verdict.chatbot_visible is False


def test_fast_parse_field_confidence_independent() -> None:
    catalog = CatalogHints(
        merchants=(
            EntityCandidate("1", "Teknosa", "Teknosa", aliases=("teknoksa",), entity_type="merchant"),
        ),
        brands=(
            EntityCandidate("b1", "Apple", "Apple", entity_type="brand"),
        ),
        categories=(
            EntityCandidate("c1", "Laptop", "Laptop", aliases=("dizüstü",), entity_type="category"),
        ),
        institutions=(
            EntityCandidate("i1", "Yapı Kredi", "Yapi Kredi", entity_type="institution"),
        ),
    )
    parse = fast_parse("Teknoksa’dan 40 bin liraya laptop", catalog=catalog)
    assert parse.route == "FAST_PATH"
    assert parse.field_confidence["budget"] == 1.0
    assert parse.field_confidence["category"] >= 0.9
    assert parse.field_confidence.get("institution", 0) == 0.0
    assert "entities" in parse.to_dict()
    assert parse.to_dict()["overall_confidence"] == parse.confidence


def test_apple_clarification_route() -> None:
    catalog = CatalogHints(
        brands=(EntityCandidate("b1", "Apple", "Apple", entity_type="brand"),),
        categories=(
            EntityCandidate("c1", "Telefon", "Telefon", entity_type="category"),
            EntityCandidate("c2", "Tablet", "Tablet", entity_type="category"),
        ),
    )
    parse = fast_parse("Apple almak istiyorum", catalog=catalog)
    assert parse.route == "CLARIFICATION_REQUIRED"
    gaps = detect_gaps(parse, category_candidates=[{"id": "c1", "display_name": "Telefon"}])
    assert gaps.clarification_viable or gaps.confidence_band in {"MEDIUM", "LOW"}

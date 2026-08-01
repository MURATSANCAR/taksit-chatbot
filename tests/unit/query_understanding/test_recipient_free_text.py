"""Recipient/kinship spans must not become required free-text categories.

Colloquial abbreviations (tel) resolve via catalog synonyms / alias_index
(ADR-010 §32) — not a static query→entity map in the parser.
"""

from __future__ import annotations

from taksitlio.entity_resolution import EntityCandidate
from taksitlio.progressive_results.category_match import (
    CATEGORY_FAMILIES,
    matches_required_categories,
)
from taksitlio.query_understanding import fast_parse
from taksitlio.query_understanding.fast_parser import CatalogHints, _free_text_product_nouns
from taksitlio.search_sessions.orchestrator import SearchOrchestrator
from taksitlio.search_sessions.repository import InMemorySearchSessionRepository


def _mobile_phone_catalog() -> CatalogHints:
    return CatalogHints(
        categories=(
            EntityCandidate(
                entity_id="MOBILE_PHONE",
                display_name="Akıllı Telefon",
                canonical_name="Akıllı Telefon",
                aliases=("telefon", "cep telefonu", "tel", "iphone", "akıllı telefon"),
                entity_type="category",
            ),
        )
    )


def _phone_token_map() -> dict[str, tuple[str, ...]]:
    legacy = CATEGORY_FAMILIES["category-phone"]["include"]
    return {
        "MOBILE_PHONE": tuple(
            dict.fromkeys(
                [
                    "telefon",
                    "cep telefonu",
                    "tel",
                    "iphone",
                    "akıllı telefon",
                    "akıllı telefon",
                    *legacy,
                ]
            )
        )
    }


def test_babama_telefon_free_text_keeps_only_telefon() -> None:
    nouns = _free_text_product_nouns("babama telefon lazım")
    assert nouns == ["telefon"]

    parse = fast_parse("babama telefon lazım")
    ids = [c.resolved_id for c in parse.positive_categories]
    assert ids == ["free_text:telefon"]
    assert all(c.display_name == "telefon" for c in parse.positive_categories)


def test_babama_tel_resolves_via_catalog_synonym_not_intel() -> None:
    """'tel' is a MOBILE_PHONE synonym — never substring-match Intel laptops."""

    # Without catalog, free-text keeps the surface form (no static expansion).
    assert _free_text_product_nouns("babama tel lazım") == ["tel"]

    catalog = _mobile_phone_catalog()
    parse = fast_parse("babama tel lazım", catalog=catalog)
    ids = [c.resolved_id for c in parse.positive_categories]
    assert ids == ["MOBILE_PHONE"]
    assert all(c.display_name == "Akıllı Telefon" for c in parse.positive_categories)

    phone = {
        "product_id": "p-phone-1",
        "display_name": "Samsung Galaxy A54 Cep Telefonu",
        "category": "Akıllı Telefon",
        "brand": "Samsung",
        "merchant_display_name": "Teknosa",
        "price": 18000.0,
        "stock_status": "AVAILABLE",
        "price_freshness": "FRESH",
        "has_primary_image": True,
        "thumbnail_cdn_url": "https://cdn.example/a54.jpg",
        "query_relevance": 0.9,
    }
    intel_laptop = {
        "product_id": "p-laptop-1",
        "display_name": (
            "LENOVO Ideapad Slim 3/ Intel core i5-13420H/ 8 GB Ram/ "
            "512 GB SSD/ 15.3 WUXGA/ W11/ Laptop 83K100UFTR"
        ),
        "category": "Laptop",
        "brand": "LENOVO",
        "merchant_display_name": "MediaMarkt",
        "price": 25999.0,
        "stock_status": "AVAILABLE",
        "price_freshness": "FRESH",
        "has_primary_image": True,
        "thumbnail_cdn_url": "https://cdn.example/ideapad.jpg",
        "query_relevance": 0.5,
    }

    # Structural guard: short include token must not substring-match Intel.
    assert not matches_required_categories(
        intel_laptop,
        {
            "positive_categories": [
                {"display_name": "xyz", "include_tokens": ["tel"]},
            ]
        },
    )

    # Catalog-resolved MOBILE_PHONE (+ short synonym filter) → phone family.
    phone_constraint = {
        "positive_categories": [
            {
                "resolved_id": "MOBILE_PHONE",
                "display_name": "Akıllı Telefon",
                "include_tokens": ["tel"],
            }
        ]
    }
    assert matches_required_categories(phone, phone_constraint)
    assert not matches_required_categories(intel_laptop, phone_constraint)

    orch = SearchOrchestrator(
        repo=InMemorySearchSessionRepository(),
        product_pool=[intel_laptop, phone],
        catalog=catalog,
        category_token_map=_phone_token_map(),
    )
    out = orch.start(conversation_id="kin-tel-1", message="babama tel lazım")
    products = (out.get("partial_results") or out.get("results") or {}).get("products") or []
    assert products, out
    ids_out = {str(p.get("product_id") or "") for p in products}
    assert "p-phone-1" in ids_out
    assert "p-laptop-1" not in ids_out
    assert all("laptop" not in str(p.get("display_name") or "").casefold() for p in products)


def test_kinship_variants_strip_recipient_keep_product() -> None:
    cases = (
        ("anneme laptop lazım", "laptop"),
        ("babam için telefon", "telefon"),
        ("kardeşime tablet alacağım", "tablet"),
        ("eşime kulaklık", "kulaklık"),
    )
    for message, product in cases:
        nouns = _free_text_product_nouns(message)
        assert product in nouns, (message, nouns)
        assert not any(
            n.casefold()
            in {"anneme", "babam", "kardeşime", "kardesime", "eşime", "esime"}
            for n in nouns
        ), (message, nouns)


def test_babama_telefon_returns_phone_not_empty_reply() -> None:
    phone = {
        "product_id": "p-phone-1",
        "display_name": "Samsung Galaxy A54 Cep Telefonu",
        "category": "Akıllı Telefon",
        "brand": "Samsung",
        "merchant_display_name": "Teknosa",
        "price": 18000.0,
        "stock_status": "AVAILABLE",
        "price_freshness": "FRESH",
        "has_primary_image": True,
        "thumbnail_cdn_url": "https://cdn.example/a54.jpg",
        "query_relevance": 0.9,
    }
    orch = SearchOrchestrator(
        repo=InMemorySearchSessionRepository(),
        product_pool=[phone],
    )
    out = orch.start(conversation_id="kin-1", message="babama telefon lazım")
    products = (out.get("partial_results") or out.get("results") or {}).get("products") or []
    assert products, out
    assert "bulamadım" not in str(out.get("reply") or "").casefold()

    # Regression: kinship must not become a required free-text category.
    assert "babama" not in {
        str(c.display_name or "").casefold()
        for c in fast_parse("babama telefon lazım").positive_categories
    }
    assert not matches_required_categories(
        phone,
        {
            "positive_categories": [
                {"display_name": "babama", "include_tokens": ["babama"]},
            ]
        },
    )
    assert matches_required_categories(
        phone,
        {
            "positive_categories": [
                {"display_name": "telefon", "include_tokens": ["telefon"]},
            ]
        },
    )

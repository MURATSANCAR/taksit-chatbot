"""Recipient/kinship spans must not become required free-text categories."""

from __future__ import annotations

from taksitlio.progressive_results.category_match import matches_required_categories
from taksitlio.query_understanding import fast_parse
from taksitlio.query_understanding.fast_parser import _free_text_product_nouns
from taksitlio.search_sessions.orchestrator import SearchOrchestrator
from taksitlio.search_sessions.repository import InMemorySearchSessionRepository


def test_babama_telefon_free_text_keeps_only_telefon() -> None:
    nouns = _free_text_product_nouns("babama telefon lazım")
    assert nouns == ["telefon"]

    parse = fast_parse("babama telefon lazım")
    ids = [c.resolved_id for c in parse.positive_categories]
    assert ids == ["free_text:telefon"]
    assert all(c.display_name == "telefon" for c in parse.positive_categories)


def test_babama_tel_expands_to_telefon_not_intel_substring() -> None:
    """Colloquial 'tel' must become telefon — never substring-match Intel laptops."""

    nouns = _free_text_product_nouns("babama tel lazım")
    assert nouns == ["telefon"]

    parse = fast_parse("babama tel lazım")
    ids = [c.resolved_id for c in parse.positive_categories]
    assert ids == ["free_text:telefon"]

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
    # Defense in depth: raw include_tokens=["tel"] must not hit Intel laptops,
    # and must still resolve to the phone family (tel → category-phone).
    tel_constraint = {
        "positive_categories": [
            {"display_name": "tel", "include_tokens": ["tel"]},
        ]
    }
    assert matches_required_categories(phone, tel_constraint)
    assert not matches_required_categories(intel_laptop, tel_constraint)

    # Bare short token without family mapping must not substring-match Intel.
    assert not matches_required_categories(
        intel_laptop,
        {
            "positive_categories": [
                {"display_name": "xyz", "include_tokens": ["tel"]},
            ]
        },
    )

    orch = SearchOrchestrator(
        repo=InMemorySearchSessionRepository(),
        product_pool=[intel_laptop, phone],
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
    # Dual free-text AND emptied the pool; matcher is OR across positives, so
    # babama+telefon would still match — the guard is the parser (no babama noun).
    assert "babama" not in {
        str(c.display_name or "").casefold() for c in fast_parse("babama telefon lazım").positive_categories
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

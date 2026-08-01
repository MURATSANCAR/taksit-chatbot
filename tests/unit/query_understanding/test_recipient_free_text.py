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

    # Regression: dual free-text AND must not empty the pool.
    bad = {
        "positive_categories": [
            {"display_name": "babama", "include_tokens": ["babama"]},
            {"display_name": "telefon", "include_tokens": ["telefon"]},
        ]
    }
    assert not matches_required_categories(phone, bad)
    good = {
        "positive_categories": [
            {"display_name": "telefon", "include_tokens": ["telefon"]},
        ]
    }
    assert matches_required_categories(phone, good)

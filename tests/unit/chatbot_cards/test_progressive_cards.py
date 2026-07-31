"""ADR-010 P5 — progressive product cards."""

from __future__ import annotations

from taksitlio.chatbot_cards import (
    CardSourceProduct,
    ProductCardFinanceSummary,
    ResponsePhase,
    build_finance_enriched_phase,
    build_first_cards_phase,
    build_searching_phase,
    card_to_public_dict,
)
from taksitlio.media.primary import IMAGE_UNAVAILABLE
from taksitlio.product_query.ranking import RankedProduct


def _source(**kwargs) -> CardSourceProduct:
    base = dict(
        product_id="p1",
        display_name="Laptop 16GB",
        brand_model="Example / EL-16",
        merchant_display_name="Example Merchant",
        price=42999.0,
        stock_status="AVAILABLE",
        has_primary_image=True,
        thumbnail_cdn_url="https://cdn.example.test/media/w640.webp",
        price_checked_at="2026-07-31T10:00:00Z",
        best_finance=ProductCardFinanceSummary(
            institution_display_name="Example Bank",
            term_months=12,
            monthly_payment=4000.0,
            total_repayment=48000.0,
            display_label="Tahmini aylık ödeme",
            campaign_ends_at="2026-08-31",
        ),
    )
    base.update(kwargs)
    return CardSourceProduct(**base)


def test_searching_phase() -> None:
    phase = build_searching_phase()
    assert phase.phase is ResponsePhase.SEARCHING
    assert phase.cards == ()


def test_first_cards_omit_finance_and_use_cdn() -> None:
    ranked = (
        RankedProduct("p1", 1.0, "En düşük aylık ödeme", False, ()),
    )
    phase = build_first_cards_phase((_source(),), ranked)
    assert phase.phase is ResponsePhase.FIRST_CARDS
    card = phase.cards[0]
    assert card.best_finance is None
    assert card.image.status == "READY"
    assert card.image.thumbnail_cdn_url.startswith("https://cdn.")
    public = card_to_public_dict(card)
    assert "merchant.example" not in str(public)


def test_finance_enriched_includes_estimate_label() -> None:
    phase = build_finance_enriched_phase((_source(),))
    assert phase.cards[0].best_finance is not None
    assert phase.cards[0].best_finance.display_label == "Tahmini aylık ödeme"


def test_missing_image_is_unavailable() -> None:
    phase = build_first_cards_phase(
        (_source(has_primary_image=False, thumbnail_cdn_url=None),)
    )
    assert phase.cards[0].image.status == IMAGE_UNAVAILABLE

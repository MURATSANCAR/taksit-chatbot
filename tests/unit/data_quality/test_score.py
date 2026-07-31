"""ADR-010 data quality scoring."""

from __future__ import annotations

from taksitlio.data_quality import (
    DataQualityStatus,
    filter_chatbot_visible,
    score_product_quality,
    signals_from_normalized,
)


def test_ready_product() -> None:
    v = score_product_quality(
        signals_from_normalized(
            external_product_id="sku-1",
            display_name="Laptop",
            price=12000,
            currency="TRY",
            stock_status="AVAILABLE",
            has_primary_image=True,
            image_cdn_ready=True,
            source_reference="src:1",
            price_fresh=True,
        )
    )
    assert v.status is DataQualityStatus.READY
    assert v.chatbot_visible
    assert v.score >= 0.9


def test_hotlink_quarantine() -> None:
    v = score_product_quality(
        signals_from_normalized(
            external_product_id="sku-1",
            display_name="Laptop",
            price=12000,
            currency="TRY",
            stock_status="AVAILABLE",
            has_primary_image=True,
            image_cdn_ready=False,
            source_reference="src:1",
            forbidden_hotlink_image=True,
        )
    )
    assert v.status is DataQualityStatus.QUARANTINED
    assert not v.chatbot_visible


def test_missing_price_quarantine() -> None:
    v = score_product_quality(
        signals_from_normalized(
            external_product_id="sku-1",
            display_name="Laptop",
            price=None,
            currency="TRY",
            stock_status="AVAILABLE",
            has_primary_image=True,
            image_cdn_ready=True,
            source_reference="src:1",
        )
    )
    assert v.status is DataQualityStatus.QUARANTINED


def test_partial_without_cdn_image_still_visible() -> None:
    v = score_product_quality(
        signals_from_normalized(
            external_product_id="sku-1",
            display_name="Laptop",
            price=12000,
            currency="TRY",
            stock_status="AVAILABLE",
            has_primary_image=True,
            image_cdn_ready=False,
            source_reference="src:1",
            price_fresh=True,
        )
    )
    assert v.status is DataQualityStatus.PARTIAL
    assert v.chatbot_visible
    assert "image_not_cdn_ready" in v.reasons


def test_filter_chatbot_visible() -> None:
    ready = score_product_quality(
        signals_from_normalized(
            external_product_id="a",
            display_name="A",
            price=1,
            currency="TRY",
            stock_status="AVAILABLE",
            has_primary_image=True,
            image_cdn_ready=True,
            source_reference="s",
            price_fresh=True,
        )
    )
    bad = score_product_quality(
        signals_from_normalized(
            external_product_id="b",
            display_name=None,
            price=None,
            currency=None,
            stock_status=None,
            has_primary_image=False,
            image_cdn_ready=False,
            source_reference=None,
            parse_failed=True,
        )
    )
    assert filter_chatbot_visible((("a", ready), ("b", bad))) == ("a",)

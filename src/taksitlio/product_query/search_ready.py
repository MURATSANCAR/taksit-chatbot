"""Search-ready projection rebuild (merchant READY + quality gates)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence


@dataclass(frozen=True)
class SearchReadyRow:
    product_id: int
    offer_id: Optional[int]
    merchant_id: int
    category_id: Optional[int]
    brand_id: Optional[int]
    readiness_status: str
    card_media_id: Optional[int]
    current_price: Optional[float]
    currency: Optional[str]
    stock_status: Optional[str]
    checkout_url_present: bool
    finance_ready: bool
    catalog_revision: str
    readiness_policy_version: Optional[str]
    media_quality_policy_version: Optional[str]


def eligible_for_search_ready(
    *,
    merchant_readiness_status: str,
    category_id: Optional[int],
    has_active_offer: bool,
    price: Optional[float],
    checkout_url_present: bool,
    card_media_ready: bool,
    product_status: str = "ACTIVE",
) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if merchant_readiness_status != "READY":
        reasons.append("merchant_not_ready")
    if product_status != "ACTIVE":
        reasons.append("product_inactive")
    if category_id is None:
        reasons.append("category_unresolved")
    if not has_active_offer:
        reasons.append("no_active_offer")
    if price is None or price <= 0:
        reasons.append("invalid_price")
    if not checkout_url_present:
        reasons.append("missing_url")
    if not card_media_ready:
        reasons.append("card_media_not_ready")
    return (not reasons), tuple(reasons)


def build_search_ready_rows(
    products: Sequence[Mapping[str, Any]],
    *,
    merchant_status_by_id: Mapping[int, str],
    catalog_revision: str,
    readiness_policy_version: Optional[str] = None,
    media_quality_policy_version: Optional[str] = None,
) -> tuple[SearchReadyRow, ...]:
    rows: list[SearchReadyRow] = []
    for p in products:
        mid = int(p["merchant_id"])
        ok, _ = eligible_for_search_ready(
            merchant_readiness_status=merchant_status_by_id.get(mid, "BLOCKED"),
            category_id=p.get("category_id"),
            has_active_offer=bool(p.get("offer_id")),
            price=p.get("current_price"),
            checkout_url_present=bool(p.get("checkout_url_present")),
            card_media_ready=bool(p.get("card_media_ready")),
            product_status=str(p.get("status") or "ACTIVE"),
        )
        if not ok:
            continue
        rows.append(
            SearchReadyRow(
                product_id=int(p["product_id"]),
                offer_id=int(p["offer_id"]) if p.get("offer_id") is not None else None,
                merchant_id=mid,
                category_id=int(p["category_id"]) if p.get("category_id") is not None else None,
                brand_id=int(p["brand_id"]) if p.get("brand_id") is not None else None,
                readiness_status="READY",
                card_media_id=int(p["card_media_id"])
                if p.get("card_media_id") is not None
                else None,
                current_price=float(p["current_price"])
                if p.get("current_price") is not None
                else None,
                currency=p.get("currency"),
                stock_status=p.get("stock_status"),
                checkout_url_present=bool(p.get("checkout_url_present")),
                finance_ready=bool(p.get("finance_ready")),
                catalog_revision=catalog_revision,
                readiness_policy_version=readiness_policy_version,
                media_quality_policy_version=media_quality_policy_version,
            )
        )
    return tuple(rows)


__all__ = [
    "SearchReadyRow",
    "build_search_ready_rows",
    "eligible_for_search_ready",
]

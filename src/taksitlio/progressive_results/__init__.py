"""Progressive / partial product retrieval while LLM runs (ADR-011 §19, §30)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from taksitlio.progressive_results.category_match import matches_required_categories


PARTIAL_LABEL = "Ön sonuçlar"


@dataclass
class PartialProduct:
    product_id: str
    display_name: str
    merchant_display_name: str
    price: float
    score: float
    thumbnail_cdn_url: Optional[str] = None
    merchant_logo_cdn_url: Optional[str] = None
    merchant_code: Optional[str] = None
    stock_status: Optional[str] = None
    best_finance_summary: Optional[dict[str, Any]] = None


@dataclass
class PartialResultSnapshot:
    query_version: int
    products: list[PartialProduct] = field(default_factory=list)
    label: str = PARTIAL_LABEL
    certainty_note: str = "Kesin uygunluk henüz doğrulanmadı"

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "certainty_note": self.certainty_note,
            "query_version": self.query_version,
            "products": [
                {
                    "product_id": p.product_id,
                    "display_name": p.display_name,
                    "merchant_display_name": p.merchant_display_name,
                    "merchant_logo_cdn_url": p.merchant_logo_cdn_url,
                    "merchant_code": p.merchant_code,
                    "stock_status": p.stock_status,
                    "price": p.price,
                    "score": p.score,
                    "thumbnail_cdn_url": p.thumbnail_cdn_url,
                    "best_finance_summary": p.best_finance_summary,
                }
                for p in self.products
            ],
        }


def can_partial_retrieve(constraints: dict[str, Any]) -> bool:
    return bool(
        constraints.get("positive_categories")
        or constraints.get("category_candidates")
        or constraints.get("budget")
        or constraints.get("merchant")
        or constraints.get("brands")
        or constraints.get("product_type")
    )


def score_partial_candidate(product: dict[str, Any], constraints: dict[str, Any]) -> float:
    # Hard exclude locked / negative categories (ADR-012 NEGATIVE_CONSTRAINT_GATE)
    if _matches_negative_constraint(product, constraints):
        return float("-inf")
    if not matches_required_categories(product, constraints):
        return float("-inf")
    score = float(product.get("query_relevance") or 0.5)
    score += 0.15 if product.get("stock_status") == "AVAILABLE" else -0.2
    freshness = str(product.get("price_freshness") or "")
    if freshness == "FRESH":
        score += 0.1
    elif freshness in {"STALE", "EXPIRED"}:
        score -= 0.15
    if product.get("has_primary_image"):
        score += 0.05
    if product.get("best_finance") or product.get("best_monthly_payment"):
        score += 0.12
    # LLM-inferred preferences must NOT act as hard filters here
    budget = constraints.get("budget") or {}
    price = float(product.get("price") or 0)
    if budget.get("maximum") and price > float(budget["maximum"]) * 1.05:
        score -= 0.35
    if budget.get("value") and abs(price - float(budget["value"])) / max(float(budget["value"]), 1) < 0.15:
        score += 0.1
    # Soft budget band when user said "~40.000 TL"
    if budget.get("value") and price > 0:
        target = float(budget["value"])
        ratio = price / target
        if ratio > 1.6 or ratio < 0.35:
            score -= 0.45
    # Uncertainty penalty when category unresolved
    if not constraints.get("positive_categories"):
        score -= 0.1
    return score


def _matches_negative_constraint(product: dict[str, Any], constraints: dict[str, Any]) -> bool:
    negatives = constraints.get("negative_categories") or constraints.get("excluded_categories") or []
    if not negatives:
        return False
    haystack = " ".join(
        str(x)
        for x in (
            product.get("display_name"),
            product.get("category"),
            product.get("category_name"),
            product.get("brand_model"),
            " ".join(str(t) for t in (product.get("tags") or ())),
        )
        if x
    ).casefold()
    for neg in negatives:
        if isinstance(neg, Mapping):
            token = str(neg.get("display_name") or neg.get("concept") or "").strip()
        else:
            token = str(neg).strip()
        if token and token.casefold() in haystack:
            return True
    return False


def build_partial_snapshot(
    *,
    query_version: int,
    products: Sequence[dict[str, Any]],
    constraints: dict[str, Any],
    limit: int = 18,
) -> PartialResultSnapshot:
    if not can_partial_retrieve(constraints):
        return PartialResultSnapshot(query_version=query_version, products=[])
    eligible = [
        p
        for p in products
        if not _matches_negative_constraint(p, constraints)
        and matches_required_categories(p, constraints)
    ]
    ranked = sorted(
        eligible,
        key=lambda p: score_partial_candidate(p, constraints),
        reverse=True,
    )[:limit]
    out = [
        PartialProduct(
            product_id=str(p.get("product_id")),
            display_name=str(p.get("display_name") or ""),
            merchant_display_name=str(p.get("merchant_display_name") or ""),
            price=float(p.get("price") or 0),
            score=score_partial_candidate(p, constraints),
            thumbnail_cdn_url=p.get("thumbnail_cdn_url"),
            merchant_logo_cdn_url=p.get("merchant_logo_cdn_url"),
            merchant_code=str(p["merchant_code"]) if p.get("merchant_code") else None,
            stock_status=str(p["stock_status"]) if p.get("stock_status") else None,
            best_finance_summary=p.get("best_finance"),
        )
        for p in ranked
    ]
    return PartialResultSnapshot(query_version=query_version, products=out)


__all__ = [
    "PARTIAL_LABEL",
    "PartialProduct",
    "PartialResultSnapshot",
    "build_partial_snapshot",
    "can_partial_retrieve",
    "score_partial_candidate",
]

"""In-memory / Postgres-ready finance option index for catalog search (ADR-010 P11)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional, Protocol, Sequence

from taksitlio.chatbot_cards import ProductCardFinanceSummary
from taksitlio.product_query.finance_projection import ProductFinanceOptionRow
from taksitlio.product_query.search import SearchProductCandidate


class FinanceOptionIndex(Protocol):
    async def list_for_product(
        self, product_id: str
    ) -> Sequence[ProductFinanceOptionRow]: ...

    async def put(
        self, product_id: str, rows: Sequence[ProductFinanceOptionRow]
    ) -> None: ...


class InMemoryFinanceOptionIndex:
    def __init__(self) -> None:
        self._by_product: dict[str, tuple[ProductFinanceOptionRow, ...]] = {}

    async def list_for_product(
        self, product_id: str
    ) -> Sequence[ProductFinanceOptionRow]:
        return self._by_product.get(str(product_id), ())

    async def list_for_products(
        self, product_ids: Sequence[str]
    ) -> dict[str, tuple[ProductFinanceOptionRow, ...]]:
        return {str(pid): self._by_product.get(str(pid), ()) for pid in product_ids}

    async def put(
        self, product_id: str, rows: Sequence[ProductFinanceOptionRow]
    ) -> None:
        self._by_product[str(product_id)] = tuple(rows)


@dataclass(frozen=True)
class InstitutionLabelResolver:
    """Maps institution_id → display label + optional logo CDN from catalog."""

    labels: dict[str, str]
    logos: dict[str, str] = field(default_factory=dict)

    def label_for(self, institution_id: str) -> str:
        return self.labels.get(institution_id) or f"institution:{institution_id}"

    def logo_cdn_url_for(self, institution_id: str) -> Optional[str]:
        return self.logos.get(str(institution_id))


def pick_best_eligible(
    rows: Sequence[ProductFinanceOptionRow],
) -> Optional[ProductFinanceOptionRow]:
    eligible = [
        r
        for r in rows
        if r.eligibility_status == "ELIGIBLE"
        and r.monthly_payment is not None
        and r.total_repayment is not None
        and r.freshness_status == "FRESH"
    ]
    if not eligible:
        return None
    return min(eligible, key=lambda r: (r.monthly_payment or 1e18, r.total_repayment or 1e18))


def enrich_candidate_with_finance(
    candidate: SearchProductCandidate,
    rows: Sequence[ProductFinanceOptionRow],
    *,
    institutions: Optional[InstitutionLabelResolver] = None,
) -> SearchProductCandidate:
    best = pick_best_eligible(rows)
    if best is None:
        return candidate
    labels = institutions or InstitutionLabelResolver(labels={})
    card = ProductCardFinanceSummary(
        institution_display_name=labels.label_for(best.institution_id),
        term_months=best.term_months,
        monthly_payment=float(best.monthly_payment or 0),
        total_repayment=float(best.total_repayment or 0),
        display_label=best.display_label or "Tahmini aylık ödeme",
        fees_total=float(best.fees_total or 0),
        institution_logo_cdn_url=labels.logo_cdn_url_for(best.institution_id),
        payment_calculation_id=(
            f"pay:{best.rate_snapshot_id or best.campaign_id or best.product_offer_id}"
            f":{best.term_months}"
        ),
        rate_snapshot_id=best.rate_snapshot_id,
        campaign_version_id=best.campaign_id,
        merchant_finance_agreement_id=(
            f"agr:{best.merchant_id}:{best.institution_id}"
        ),
    )
    return replace(
        candidate,
        best_monthly_payment=card.monthly_payment,
        best_total_repayment=card.total_repayment,
        best_term_months=card.term_months,
        finance_active=True,
        rate_fresh=best.freshness_status == "FRESH",
        campaign_active=True,
        card_finance=card,
    )


class InMemoryInstitutionLabelLoader:
    """Mutable label source for local/dev; production uses Postgres loader."""

    def __init__(self, labels: Optional[dict[str, str]] = None) -> None:
        self._labels: dict[str, str] = dict(labels or {})

    def set_labels(self, labels: dict[str, str]) -> None:
        self._labels = dict(labels)

    async def load_labels(self) -> dict[str, str]:
        return dict(self._labels)


async def load_institution_labels(loader: object) -> InstitutionLabelResolver:
    """Build resolver from a loader exposing ``async load_labels()`` (+ optional logos)."""

    labels = await loader.load_labels()  # type: ignore[attr-defined]
    logos: dict[str, str] = {}
    load_logos = getattr(loader, "load_logos", None)
    if callable(load_logos):
        logos = dict(await load_logos() or {})
    return InstitutionLabelResolver(labels=dict(labels or {}), logos=logos)


__all__ = [
    "FinanceOptionIndex",
    "InMemoryFinanceOptionIndex",
    "InMemoryInstitutionLabelLoader",
    "InstitutionLabelResolver",
    "enrich_candidate_with_finance",
    "load_institution_labels",
    "pick_best_eligible",
]

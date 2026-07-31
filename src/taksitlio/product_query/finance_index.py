"""In-memory / Postgres-ready finance option index for catalog search (ADR-010 P11)."""

from __future__ import annotations

from dataclasses import dataclass, replace
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

    async def put(
        self, product_id: str, rows: Sequence[ProductFinanceOptionRow]
    ) -> None:
        self._by_product[str(product_id)] = tuple(rows)


@dataclass(frozen=True)
class InstitutionLabelResolver:
    """Maps institution_id → display label from catalog; no hardcoded bank names."""

    labels: dict[str, str]

    def label_for(self, institution_id: str) -> str:
        return self.labels.get(institution_id) or f"institution:{institution_id}"


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
    """Build resolver from a loader exposing ``async load_labels() -> dict``."""

    labels = await loader.load_labels()  # type: ignore[attr-defined]
    return InstitutionLabelResolver(labels=dict(labels or {}))


__all__ = [
    "FinanceOptionIndex",
    "InMemoryFinanceOptionIndex",
    "InMemoryInstitutionLabelLoader",
    "InstitutionLabelResolver",
    "enrich_candidate_with_finance",
    "load_institution_labels",
    "pick_best_eligible",
]

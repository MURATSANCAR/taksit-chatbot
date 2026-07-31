"""Fast product query path (ADR-010 §52) — P4 orchestration stubs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from taksitlio.entity_resolution import (
    EntityCandidate,
    ResolutionPolicy,
    ResolutionResult,
    resolve_entity,
)
from taksitlio.product_query.finance_projection import (
    InstitutionTermOption,
    OfferFinanceContext,
    ProductFinanceOptionRow,
    rebuild_finance_options,
)
from taksitlio.product_query.ranking import (
    RankableProduct,
    RankedProduct,
    RankingMode,
    RankingWeights,
    rank_products,
)

ADR_SCOPE = "ADR-010"
PACKAGE_STATUS = "P4"


@dataclass(frozen=True)
class StructuredProductFilters:
    merchant_id: Optional[str] = None
    institution_ids: tuple[str, ...] = ()
    max_price: Optional[float] = None
    term_months: Optional[int] = None
    required_attributes: MappingLike = None  # patched below


from typing import Any, Mapping  # noqa: E402

StructuredProductFilters.__annotations__["required_attributes"] = Mapping[str, Any]


@dataclass(frozen=True)
class ProductQueryRequest:
    utterance: str
    ranking_mode: RankingMode = RankingMode.BEST_OVERALL_VALUE
    filters: Optional[StructuredProductFilters] = None


@dataclass(frozen=True)
class ProductQueryResult:
    merchant_resolution: Optional[ResolutionResult]
    institution_resolutions: tuple[ResolutionResult, ...]
    ranked: tuple[RankedProduct, ...]
    finance_options: tuple[ProductFinanceOptionRow, ...]


def resolve_merchant(
    text: str,
    catalog: Sequence[EntityCandidate],
    *,
    policy: Optional[ResolutionPolicy] = None,
) -> ResolutionResult:
    return resolve_entity(text, catalog, policy=policy)


def resolve_institutions(
    texts: Sequence[str],
    catalog: Sequence[EntityCandidate],
    *,
    policy: Optional[ResolutionPolicy] = None,
) -> tuple[ResolutionResult, ...]:
    return tuple(resolve_entity(t, catalog, policy=policy) for t in texts)


def run_product_query(
    request: ProductQueryRequest,
    *,
    merchant_catalog: Sequence[EntityCandidate] = (),
    institution_catalog: Sequence[EntityCandidate] = (),
    merchant_query: Optional[str] = None,
    institution_queries: Sequence[str] = (),
    products: Sequence[RankableProduct] = (),
    offer: Optional[OfferFinanceContext] = None,
    finance_term_options: Sequence[InstitutionTermOption] = (),
    ranking_weights: Optional[RankingWeights] = None,
    resolution_policy: Optional[ResolutionPolicy] = None,
) -> ProductQueryResult:
    """Compose resolution → finance projection → ranking without DB I/O."""

    merchant_res = (
        resolve_merchant(merchant_query, merchant_catalog, policy=resolution_policy)
        if merchant_query
        else None
    )
    inst_res = resolve_institutions(
        institution_queries, institution_catalog, policy=resolution_policy
    )
    finance_rows = (
        rebuild_finance_options(offer, finance_term_options)
        if offer is not None
        else ()
    )
    ranked = rank_products(
        products,
        mode=request.ranking_mode,
        weights=ranking_weights,
    )
    return ProductQueryResult(
        merchant_resolution=merchant_res,
        institution_resolutions=inst_res,
        ranked=ranked,
        finance_options=finance_rows,
    )


__all__ = [
    "ADR_SCOPE",
    "PACKAGE_STATUS",
    "ProductQueryRequest",
    "ProductQueryResult",
    "StructuredProductFilters",
    "rebuild_finance_options",
    "rank_products",
    "RankingMode",
    "resolve_institutions",
    "resolve_merchant",
    "run_product_query",
]

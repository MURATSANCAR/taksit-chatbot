"""Bridge chat need_profile → catalog product search (ADR-010 P13).

No invented merchants/rates; catalog + finance index only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from taksitlio.chatbot_cards import ProgressiveResponse, card_to_public_dict
from taksitlio.product_query.ranking import RankingMode
from taksitlio.product_query.search import (
    ProductSearchRequest,
    ProductSearchResponse,
    search_products,
)


@dataclass(frozen=True)
class ProductPathDeps:
    """Optional catalog search deps for ChatPipeline."""

    catalog: Any
    merchant_directory: Any = None
    finance_index: Any = None
    institution_labels: Any = None
    alias_cache: Any = None
    alias_ttl_seconds: int = 300
    enabled: bool = True
    catalog_limit: int = 40


def budget_max_price(need_profile: Mapping[str, Any]) -> Optional[float]:
    budget = need_profile.get("budget") or {}
    if not isinstance(budget, Mapping):
        return None
    for key in ("maximum", "value"):
        raw = budget.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def infer_ranking_mode(need_profile: Mapping[str, Any]) -> RankingMode:
    """Prefer browse-safe cheapest until monthly-payment budget is explicit."""

    budget = need_profile.get("budget") or {}
    if isinstance(budget, Mapping):
        btype = str(budget.get("type") or "").upper()
        if btype == "MONTHLY_PAYMENT" or budget.get("monthly_payment") is not None:
            return RankingMode.LOWEST_MONTHLY_PAYMENT
    return RankingMode.CHEAPEST_PRODUCT_PRICE


def _entity_texts(
    need_profile: Mapping[str, Any], allowed_types: set[str]
) -> list[str]:
    out: list[str] = []
    for ent in need_profile.get("entities") or []:
        if not isinstance(ent, Mapping):
            continue
        etype = str(ent.get("type") or ent.get("entity_type") or "").upper()
        if etype not in allowed_types:
            continue
        text = ent.get("text") or ent.get("value") or ent.get("normalized")
        if text:
            out.append(str(text).strip())
    return [t for t in out if t]


def need_profile_to_search_request(
    message: str,
    need_profile: Mapping[str, Any],
    *,
    phase: str = "FINANCE_ENRICHED",
) -> ProductSearchRequest:
    utterance = str(need_profile.get("need_description") or message).strip() or message
    merchants = _entity_texts(need_profile, {"MERCHANT", "STORE", "RETAILER"})
    institutions = _entity_texts(
        need_profile, {"INSTITUTION", "BANK", "FINANCE", "FINANCIAL_INSTITUTION"}
    )
    term: Optional[int] = None
    for ent in need_profile.get("entities") or []:
        if not isinstance(ent, Mapping):
            continue
        if str(ent.get("type") or "").upper() in {"TERM", "INSTALLMENT_TERM"}:
            raw = ent.get("value") or ent.get("text")
            try:
                term = int(raw)
            except (TypeError, ValueError):
                term = None
            break

    return ProductSearchRequest(
        utterance=utterance,
        merchant_text=merchants[0] if merchants else None,
        institution_texts=tuple(institutions),
        ranking_mode=infer_ranking_mode(need_profile),
        max_price=budget_max_price(need_profile),
        requested_term=term,
        phase=phase,
    )


@dataclass(frozen=True)
class ChatProductSearchResult:
    search: ProductSearchResponse
    cards: tuple[dict[str, Any], ...]
    phase: str
    used_catalog: bool


async def run_catalog_search_for_chat(
    deps: ProductPathDeps,
    request: ProductSearchRequest,
    *,
    merchant_catalog: Sequence[Any] = (),
    institution_catalog: Sequence[Any] = (),
) -> Optional[ChatProductSearchResult]:
    """Load catalog candidates and search. Returns None if path disabled/empty catalog."""

    if not deps.enabled or deps.catalog is None:
        return None

    from taksitlio.product_query.candidates import load_search_candidates_from_catalog

    products = list(
        await load_search_candidates_from_catalog(
            deps.catalog,
            utterance=request.utterance,
            limit=deps.catalog_limit,
            merchants=deps.merchant_directory,
            finance_index=deps.finance_index,
            institutions=deps.institution_labels,
        )
    )
    if not products:
        return None

    result = await search_products(
        request,
        products=products,
        merchant_catalog=merchant_catalog,
        institution_catalog=institution_catalog,
        cache=deps.alias_cache,
        alias_ttl_seconds=deps.alias_ttl_seconds,
    )
    cards = tuple(card_to_public_dict(c) for c in result.result_phase.cards)
    return ChatProductSearchResult(
        search=result,
        cards=cards,
        phase=result.result_phase.phase.value,
        used_catalog=True,
    )


def progressive_for_reply(result: ChatProductSearchResult) -> ProgressiveResponse:
    return result.search.result_phase


__all__ = [
    "ChatProductSearchResult",
    "ProductPathDeps",
    "budget_max_price",
    "infer_ranking_mode",
    "need_profile_to_search_request",
    "progressive_for_reply",
    "run_catalog_search_for_chat",
]

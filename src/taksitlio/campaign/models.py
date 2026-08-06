"""Campaign domain models and retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, Sequence


@dataclass(frozen=True)
class Campaign:
    id: int
    campaign_code: str
    title: str
    summary: str
    category_code: str
    category_id: int
    brand: str | None = None
    product_name: str | None = None
    list_price: float | None = None
    currency: str = "TRY"
    installment_count: int | None = None
    monthly_payment: float | None = None
    cash_price: float | None = None
    min_budget: float | None = None
    max_budget: float | None = None
    membership_required: bool = True
    membership_cta_url: str | None = None
    membership_cta_label: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    status: str = "ACTIVE"
    attributes: dict[str, Any] = field(default_factory=dict)
    search_text: str = ""
    semantic_score: float = 0.0

    def to_grounding_dict(self) -> dict[str, Any]:
        return {
            "campaign_code": self.campaign_code,
            "title": self.title,
            "summary": self.summary,
            "brand": self.brand,
            "product_name": self.product_name,
            "list_price": self.list_price,
            "currency": self.currency,
            "installment_count": self.installment_count,
            "monthly_payment": self.monthly_payment,
            "cash_price": self.cash_price,
            "membership_required": self.membership_required,
            "membership_cta_url": self.membership_cta_url,
            "membership_cta_label": self.membership_cta_label,
            "attributes": self.attributes,
        }


class CampaignRepository(Protocol):
    async def list_by_category_codes(
        self,
        category_codes: Sequence[str],
        *,
        limit: int = 50,
    ) -> list[Campaign]: ...

    async def get_eligibility_rules(self, rule_set_code: str = "DEFAULT") -> list[dict[str, Any]]: ...

    async def get_ranking_policy(self, policy_code: str = "DEFAULT") -> dict[str, Any]: ...


class InMemoryCampaignRepository:
    def __init__(
        self,
        campaigns: Sequence[Campaign],
        *,
        eligibility_rules: list[dict[str, Any]] | None = None,
        ranking_weights: dict[str, float] | None = None,
        max_results: int = 5,
    ) -> None:
        self._campaigns = list(campaigns)
        self._rules = eligibility_rules or [
            {"type": "STATUS_ACTIVE"},
            {"type": "WITHIN_DATE_WINDOW"},
            {"type": "BUDGET_COMPATIBLE"},
            {"type": "CATEGORY_MATCH"},
            {"type": "MONTHLY_PAYMENT_COMPATIBLE"},
        ]
        self._ranking = {
            "weights": ranking_weights
            or {
                "budget_fit": 0.35,
                "preference_fit": 0.25,
                "semantic_relevance": 0.20,
                "installment_fit": 0.15,
                "freshness": 0.05,
            },
            "max_results": max_results,
        }

    async def list_by_category_codes(
        self,
        category_codes: Sequence[str],
        *,
        limit: int = 50,
    ) -> list[Campaign]:
        codes = set(category_codes)
        matched = [c for c in self._campaigns if c.category_code in codes]
        return matched[:limit]

    async def list_active(
        self, category_id: int | None = None, locale: str = "tr-TR"
    ) -> list[Campaign]:
        """All ACTIVE campaigns — used by the guest general-finance merge."""
        return [
            c
            for c in self._campaigns
            if str(getattr(c, "status", "ACTIVE")).upper() == "ACTIVE"
            and (category_id is None or getattr(c, "category_id", None) == category_id)
        ]

    async def get_eligibility_rules(self, rule_set_code: str = "DEFAULT") -> list[dict[str, Any]]:
        return list(self._rules)

    async def get_ranking_policy(self, policy_code: str = "DEFAULT") -> dict[str, Any]:
        return dict(self._ranking)


class CampaignRetriever:
    def __init__(self, repository: CampaignRepository) -> None:
        self._repo = repository

    async def retrieve(
        self,
        category_codes: Sequence[str],
        *,
        limit: int = 50,
    ) -> list[Campaign]:
        if not category_codes:
            return []
        return await self._repo.list_by_category_codes(category_codes, limit=limit)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

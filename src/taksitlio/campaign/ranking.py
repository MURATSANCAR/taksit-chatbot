"""Dynamic ranking engine — weights loaded from ranking_policies table."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from taksitlio.campaign.models import Campaign, utcnow
from taksitlio.embeddings.vectors import cosine_similarity


@dataclass(frozen=True)
class RankedCampaign:
    campaign: Campaign
    score: float
    components: dict[str, float]


class RankingEngine:
    def rank(
        self,
        campaigns: Sequence[Campaign],
        need_profile: Mapping[str, Any],
        *,
        weights: Mapping[str, float],
        max_results: int = 5,
        query_embedding: Sequence[float] | None = None,
        campaign_embeddings: Mapping[str, Sequence[float]] | None = None,
        now: datetime | None = None,
    ) -> list[RankedCampaign]:
        clock = now or utcnow()
        ranked: list[RankedCampaign] = []
        for campaign in campaigns:
            components = {
                "budget_fit": _budget_fit(campaign, need_profile),
                "preference_fit": _preference_fit(campaign, need_profile),
                "semantic_relevance": _semantic_relevance(
                    campaign, query_embedding, campaign_embeddings
                ),
                "installment_fit": _installment_fit(campaign, need_profile),
                "freshness": _freshness(campaign, clock),
            }
            score = 0.0
            for key, weight in weights.items():
                score += float(weight) * float(components.get(key, 0.0))
            ranked.append(
                RankedCampaign(campaign=campaign, score=score, components=components)
            )
        ranked.sort(key=lambda r: r.score, reverse=True)
        return ranked[:max_results]


def _budget_fit(campaign: Campaign, need_profile: Mapping[str, Any]) -> float:
    budget = need_profile.get("budget") or {}
    btype = budget.get("type")
    price = campaign.list_price or campaign.cash_price
    if price is None:
        return 0.5
    if btype == "MONTHLY_PAYMENT":
        return _installment_fit(campaign, need_profile)
    target = budget.get("value")
    if btype == "RANGE":
        target = budget.get("maximum") or budget.get("minimum")
    if target is None:
        return 0.5
    target_f = float(target)
    if target_f <= 0:
        return 0.0
    ratio = float(price) / target_f
    if ratio <= 1.0:
        return max(0.0, 1.0 - (1.0 - ratio) * 0.3)
    # over budget
    over = ratio - 1.0
    return max(0.0, 1.0 - over * 2.0)


def _preference_fit(campaign: Campaign, need_profile: Mapping[str, Any]) -> float:
    prefs = need_profile.get("preferences") or []
    if not prefs:
        return 0.5
    attrs = campaign.attributes or {}
    total_weight = 0.0
    scored = 0.0
    for pref in prefs:
        concept = str(pref.get("concept") or "")
        importance = float(pref.get("importance") or 0.0)
        if not concept or importance <= 0:
            continue
        total_weight += importance
        attr_val = attrs.get(concept)
        if isinstance(attr_val, (int, float)):
            scored += importance * float(attr_val)
        elif concept == "installment" and campaign.installment_count:
            scored += importance * min(1.0, campaign.installment_count / 12.0)
        else:
            scored += importance * 0.4
    if total_weight <= 0:
        return 0.5
    return max(0.0, min(1.0, scored / total_weight))


def _semantic_relevance(
    campaign: Campaign,
    query_embedding: Sequence[float] | None,
    campaign_embeddings: Mapping[str, Sequence[float]] | None,
) -> float:
    if campaign.semantic_score:
        return max(0.0, min(1.0, float(campaign.semantic_score)))
    if query_embedding and campaign_embeddings:
        emb = campaign_embeddings.get(campaign.campaign_code)
        if emb:
            return max(0.0, min(1.0, cosine_similarity(query_embedding, emb)))
    # weak lexical fallback against search_text
    return 0.5


def _installment_fit(campaign: Campaign, need_profile: Mapping[str, Any]) -> float:
    budget = need_profile.get("budget") or {}
    prefs = need_profile.get("preferences") or []
    wants_installment = any(
        str(p.get("concept") or "") == "installment" for p in prefs
    )
    monthly = budget.get("monthly_payment")
    if budget.get("type") == "MONTHLY_PAYMENT":
        monthly = monthly if monthly is not None else budget.get("value")

    if monthly is not None and campaign.monthly_payment is not None:
        ratio = float(campaign.monthly_payment) / max(float(monthly), 1.0)
        if ratio <= 1.0:
            return 1.0 - (1.0 - ratio) * 0.2
        return max(0.0, 1.0 - (ratio - 1.0) * 2.5)

    if wants_installment:
        if campaign.installment_count and campaign.installment_count >= 6:
            return 0.9
        if campaign.installment_count:
            return 0.6
        return 0.2
    return 0.5


def _freshness(campaign: Campaign, now: datetime) -> float:
    starts = campaign.starts_at
    if starts is None:
        return 0.5
    if starts.tzinfo is None:
        starts = starts.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - starts).total_seconds() / 86400.0)
    if age_days <= 7:
        return 1.0
    if age_days <= 30:
        return 0.8
    if age_days <= 90:
        return 0.6
    return 0.4

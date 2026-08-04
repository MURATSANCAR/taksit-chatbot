"""Production Needs Analysis Service — real CampaignRepository interface.

Uses:
  - list_by_category_codes(category_codes)  (PostgresCampaignRepository / InMemory)
  - RankingEngine.rank(...)
  - EligibilityEngine.is_eligible(...)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class NeedsAnalysisOutcome:
    fast_success: bool = False
    category_id: Optional[int] = None
    category_code: Optional[str] = None
    category_name: Optional[str] = None
    budget_value: Optional[float] = None
    budget_type: Optional[str] = None
    intent: Optional[str] = None
    ranked_campaigns: list[dict[str, Any]] = field(default_factory=list)
    gate_status: str = "OK"
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _campaign_to_dict(c: Any) -> dict[str, Any]:
    """Normalize Campaign dataclass or dict to ranking/response shape."""
    if isinstance(c, dict):
        return c
    attrs = getattr(c, "attributes", None) or {}
    return {
        "id": getattr(c, "id", None),
        "campaign_code": getattr(c, "campaign_code", None),
        "title": getattr(c, "title", ""),
        "summary": getattr(c, "summary", ""),
        "subtitle": getattr(c, "summary", ""),
        "bank": getattr(c, "brand", None),
        "brand": getattr(c, "brand", None),
        "rate_text": attrs.get("rate_text") if isinstance(attrs, dict) else None,
        "max_amount": getattr(c, "max_budget", None),
        "max_budget": getattr(c, "max_budget", None),
        "min_budget": getattr(c, "min_budget", None),
        "max_tenure": getattr(c, "installment_count", None),
        "installment_count": getattr(c, "installment_count", None),
        "monthly_payment": getattr(c, "monthly_payment", None),
        "text": attrs.get("raw_subtitle") if isinstance(attrs, dict) else None,
        "status": getattr(c, "status", "ACTIVE"),
        "membership_required": getattr(c, "membership_required", True),
        "membership_cta_label": getattr(c, "membership_cta_label", None),
        "starts_at": getattr(c, "starts_at", None),
        "ends_at": getattr(c, "ends_at", None),
        "category_code": getattr(c, "category_code", None),
        "category_id": getattr(c, "category_id", None),
        "score": getattr(c, "semantic_score", 0.0),
        "attributes": attrs,
    }


class NeedsAnalysisService:
    def __init__(
        self,
        *,
        fast_extractor,
        semantic_matcher,
        campaign_ranker,
        eligibility_engine,
        campaign_repository,
        category_catalog=None,
        quality_gate_policy=None,
    ) -> None:
        self._fast = fast_extractor
        self._matcher = semantic_matcher
        self._ranker = campaign_ranker
        self._eligibility = eligibility_engine
        self._campaigns = campaign_repository
        self._catalog = category_catalog
        self._gate = quality_gate_policy

    async def analyse(
        self,
        *,
        utterance: str,
        session_id: str,
        locale: str = "tr-TR",
        max_recommendations: int = 2,
    ) -> NeedsAnalysisOutcome:
        outcome = NeedsAnalysisOutcome()
        diag: dict[str, Any] = {"utterance_len": len(utterance)}

        # 1. FAST
        try:
            fast_result = await self._fast.extract(utterance, locale=locale)
            outcome.fast_success = True
            outcome.intent = (fast_result.get("intent") or {}).get("type")
            budget = fast_result.get("budget") or {}
            outcome.budget_value = budget.get("value") or budget.get("maximum")
            outcome.budget_type = budget.get("type")
            diag["fast"] = {
                "intent": outcome.intent,
                "budget": outcome.budget_value,
                "signals": fast_result.get("category_signals"),
            }
        except Exception as exc:
            logger.exception("FAST extraction failed session=%s", session_id)
            outcome.fast_success = False
            outcome.gate_status = "SAFE_FAILURE"
            outcome.diagnostics = {"error": "fast_failure", "detail": str(exc)}
            return outcome

        # 2. Semantic category match
        try:
            match_query = {
                "utterance": utterance,
                "locale": locale,
                "budget": outcome.budget_value,
                "signals": fast_result.get("category_signals"),
            }
            match_result = await self._matcher.match(match_query)
            if match_result and match_result.get("status") == "MATCHED":
                outcome.category_id = match_result.get("category_id")
                outcome.category_code = str(
                    match_result.get("category_code")
                    or match_result.get("category_id")
                    or ""
                )
                outcome.category_name = match_result.get("category_name")
                diag["match"] = {
                    "category_id": outcome.category_id,
                    "category_code": outcome.category_code,
                    "score": match_result.get("score"),
                    "method": match_result.get("method"),
                }
            else:
                diag["match"] = {"status": "NO_MATCH"}
        except Exception as exc:
            logger.exception("Semantic match failed session=%s", session_id)
            diag["match_error"] = str(exc)

        if not outcome.category_id and not outcome.budget_value:
            outcome.gate_status = "CLARIFY"
            outcome.diagnostics = diag
            return outcome

        # 3. Load candidates — real repo interface
        category_codes: list[str] = []
        if outcome.category_code:
            category_codes.append(str(outcome.category_code))
        elif outcome.category_id is not None:
            category_codes.append(str(outcome.category_id))

        try:
            if hasattr(self._campaigns, "list_by_category_codes") and category_codes:
                candidates_raw = await self._campaigns.list_by_category_codes(
                    category_codes, limit=50
                )
            elif hasattr(self._campaigns, "list_active"):
                # backward-compat for older in-memory stubs
                candidates_raw = await self._campaigns.list_active(
                    category_id=outcome.category_id, locale=locale
                )
            else:
                candidates_raw = []
            candidates = [_campaign_to_dict(c) for c in candidates_raw]
            diag["candidates"] = len(candidates)
        except Exception as exc:
            logger.exception("Campaign repository failed")
            outcome.gate_status = "SAFE_FAILURE"
            outcome.diagnostics = {**diag, "error": "campaign_repo", "detail": str(exc)}
            return outcome

        if not candidates:
            outcome.diagnostics = diag
            return outcome

        # 4. Eligibility
        need_profile = {
            "budget": {
                "value": outcome.budget_value,
                "type": outcome.budget_type or "APPROXIMATE",
            },
            "intent": outcome.intent,
            "category_id": outcome.category_id,
            "category_code": outcome.category_code,
        }
        eligible = []
        for camp in candidates:
            try:
                if self._eligibility.is_eligible(camp, need_profile):
                    eligible.append(camp)
            except Exception:
                # tolerant: if eligibility signature expects Campaign object, try raw
                try:
                    if self._eligibility.is_eligible(candidates_raw[candidates.index(camp)], need_profile):
                        eligible.append(camp)
                except Exception:
                    continue
        diag["eligible"] = len(eligible)

        if not eligible:
            outcome.diagnostics = diag
            return outcome

        # 5. Ranking
        try:
            ranked = self._ranker.rank(
                campaigns=eligible,
                need_profile=need_profile,
                max_results=max_recommendations,
                weights={
                    "budget_fit": 0.40,
                    "preference_fit": 0.15,
                    "semantic_relevance": 0.20,
                    "installment_fit": 0.15,
                    "freshness": 0.10,
                },
            )
            # Ensure dict shape
            outcome.ranked_campaigns = [_campaign_to_dict(c) for c in ranked]
            diag["ranked"] = [
                {"id": c.get("id"), "score": c.get("score")} for c in outcome.ranked_campaigns
            ]
        except TypeError:
            # Some RankingEngine signatures omit weights=
            try:
                ranked = self._ranker.rank(
                    campaigns=eligible,
                    need_profile=need_profile,
                    max_results=max_recommendations,
                )
                outcome.ranked_campaigns = [_campaign_to_dict(c) for c in ranked]
            except Exception as exc:
                logger.exception("Ranking failed")
                outcome.gate_status = "SAFE_FAILURE"
                outcome.diagnostics = {**diag, "error": "ranking", "detail": str(exc)}
                return outcome
        except Exception as exc:
            logger.exception("Ranking failed")
            outcome.gate_status = "SAFE_FAILURE"
            outcome.diagnostics = {**diag, "error": "ranking", "detail": str(exc)}
            return outcome

        # 6. Gate
        if self._gate is not None:
            gate_decision = self._gate.evaluate(
                ranked=outcome.ranked_campaigns,
                need_profile=need_profile,
                context={"session_id": session_id, "locale": locale},
            )
            outcome.gate_status = gate_decision.get("status", "OK")
            diag["gate"] = gate_decision
        else:
            if (
                outcome.ranked_campaigns
                and (outcome.ranked_campaigns[0].get("score") or 0) < 0.35
            ):
                outcome.gate_status = "PROVISIONAL"
            else:
                outcome.gate_status = "OK"

        outcome.diagnostics = diag
        return outcome

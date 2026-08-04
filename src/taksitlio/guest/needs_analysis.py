"""Production Needs Analysis Service for guest (and authenticated) turns.

Orchestrates:
  FAST extraction (intent + budget + category signals)
  → Semantic category match
  → Campaign eligibility + ranking (budget_fit weighted)
  → Gate evaluation

This is the single production entry that the GuestEntryHandler and
the main ChatOrchestrator both call. Keeps the pipeline consistent
and testable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)


@dataclass
class NeedsAnalysisOutcome:
    """Structured result of a full needs-analysis pass."""

    fast_success: bool = False
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    budget_value: Optional[float] = None
    budget_type: Optional[str] = None          # RANGE | APPROXIMATE | MAXIMUM
    intent: Optional[str] = None
    ranked_campaigns: list[dict[str, Any]] = field(default_factory=list)
    gate_status: str = "OK"                    # OK | CLARIFY | SAFE_FAILURE | PROVISIONAL
    diagnostics: dict[str, Any] = field(default_factory=dict)


class NeedsAnalysisService:
    """
    Production façade over the existing internal components.

    Dependencies are injected so the service stays unit-testable and
    can run both in the real Redis/pgvector stack and in pure in-memory
    evaluation harnesses.
    """

    def __init__(
        self,
        *,
        fast_extractor,                # DeterministicFastExtractor / FastNeedUnderstanding
        semantic_matcher,              # SemanticCategoryMatcher
        campaign_ranker,               # RankingEngine from campaign/ranking.py
        eligibility_engine,            # EligibilityEngine
        campaign_repository,           # CampaignRepository
        category_catalog,              # CategoryCatalogService
        quality_gate_policy=None,      # optional QualityGate policy
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
        """
        Full production pipeline for one utterance.

        Returns a NeedsAnalysisOutcome that the caller can turn into
        grounded messages + MembershipCTA.
        """
        outcome = NeedsAnalysisOutcome()
        diag: dict[str, Any] = {"utterance_len": len(utterance)}

        # ----------------------------------------------------------
        # 1. FAST extraction (intent + budget + category signals)
        # ----------------------------------------------------------
        try:
            fast_result = await self._fast.extract(utterance, locale=locale)
            outcome.fast_success = True
            outcome.intent = fast_result.get("intent", {}).get("type")
            budget = fast_result.get("budget") or {}
            outcome.budget_value = budget.get("value") or budget.get("maximum")
            outcome.budget_type = budget.get("type")
            diag["fast"] = {
                "intent": outcome.intent,
                "budget": outcome.budget_value,
                "signals": fast_result.get("category_signals"),
            }
        except Exception as exc:
            logger.exception("FAST extraction failed for session %s", session_id)
            outcome.fast_success = False
            outcome.gate_status = "SAFE_FAILURE"
            outcome.diagnostics = {"error": "fast_failure", "detail": str(exc)}
            return outcome

        # ----------------------------------------------------------
        # 2. Semantic category match
        # ----------------------------------------------------------
        try:
            match_query = {
                "utterance": utterance,
                "locale": locale,
                "budget": outcome.budget_value,
                "signals": fast_result.get("category_signals"),
            }
            match_result = await self._matcher.match(match_query)
            if match_result and match_result.get("status") == "MATCHED":
                outcome.category_id = match_result["category_id"]
                outcome.category_name = match_result.get("category_name")
                diag["match"] = {
                    "category_id": outcome.category_id,
                    "score": match_result.get("score"),
                    "method": match_result.get("method"),
                }
            else:
                diag["match"] = {"status": "NO_MATCH"}
        except Exception as exc:
            logger.exception("Semantic match failed for session %s", session_id)
            # Non-fatal: we can still try ranking with free-text signals
            diag["match_error"] = str(exc)

        # ----------------------------------------------------------
        # 3. Early exit if we have nothing actionable
        # ----------------------------------------------------------
        if not outcome.category_id and not outcome.budget_value:
            outcome.gate_status = "CLARIFY"
            outcome.diagnostics = diag
            return outcome

        # ----------------------------------------------------------
        # 4. Load candidate campaigns (ACTIVE + date window)
        # ----------------------------------------------------------
        try:
            candidates = await self._campaigns.list_active(
                category_id=outcome.category_id,
                locale=locale,
            )
            diag["candidates"] = len(candidates)
        except Exception as exc:
            logger.exception("Campaign repository failed")
            outcome.gate_status = "SAFE_FAILURE"
            outcome.diagnostics = {**diag, "error": "campaign_repo", "detail": str(exc)}
            return outcome

        if not candidates:
            outcome.diagnostics = diag
            return outcome  # empty ranked list → caller shows "no campaign" + CTA

        # ----------------------------------------------------------
        # 5. Eligibility filter (guest-tolerant)
        # ----------------------------------------------------------
        need_profile = {
            "budget": {
                "value": outcome.budget_value,
                "type": outcome.budget_type or "APPROXIMATE",
            },
            "intent": outcome.intent,
            "category_id": outcome.category_id,
        }
        eligible = []
        for camp in candidates:
            try:
                if self._eligibility.is_eligible(camp, need_profile):
                    eligible.append(camp)
            except Exception:
                continue
        diag["eligible"] = len(eligible)

        if not eligible:
            outcome.diagnostics = diag
            return outcome

        # ----------------------------------------------------------
        # 6. Ranking (budget_fit heavily weighted for guest flow)
        # ----------------------------------------------------------
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
            outcome.ranked_campaigns = ranked
            diag["ranked"] = [
                {"id": c.get("id"), "score": c.get("score")} for c in ranked
            ]
        except Exception as exc:
            logger.exception("Ranking failed")
            outcome.gate_status = "SAFE_FAILURE"
            outcome.diagnostics = {**diag, "error": "ranking", "detail": str(exc)}
            return outcome

        # ----------------------------------------------------------
        # 7. Quality / safety gate (provisional acceptance aware)
        # ----------------------------------------------------------
        if self._gate is not None:
            gate_decision = self._gate.evaluate(
                ranked=outcome.ranked_campaigns,
                need_profile=need_profile,
                context={"session_id": session_id, "locale": locale},
            )
            outcome.gate_status = gate_decision.get("status", "OK")
            diag["gate"] = gate_decision
        else:
            # Default production behaviour when no explicit policy injected:
            # require at least one campaign with score > 0.35
            if outcome.ranked_campaigns and outcome.ranked_campaigns[0].get("score", 0) < 0.35:
                outcome.gate_status = "PROVISIONAL"
            else:
                outcome.gate_status = "OK"

        outcome.diagnostics = diag
        return outcome

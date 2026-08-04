"""Production Needs Analysis Service for guest (and authenticated) turns.

Talks to real FAST / SemanticCategoryMatcher / CampaignRepository /
EligibilityEngine / RankingEngine surfaces, while remaining duck-typed
for golden mocks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

_GUEST_RANK_WEIGHTS = {
    "budget_fit": 0.40,
    "preference_fit": 0.15,
    "semantic_relevance": 0.20,
    "installment_fit": 0.15,
    "freshness": 0.10,
}


@dataclass
class NeedsAnalysisOutcome:
    """Structured result of a full needs-analysis pass."""

    fast_success: bool = False
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    category_code: Optional[str] = None
    budget_value: Optional[float] = None
    budget_type: Optional[str] = None  # RANGE | APPROXIMATE | MAXIMUM
    intent: Optional[str] = None
    ranked_campaigns: list[dict[str, Any]] = field(default_factory=list)
    gate_status: str = "OK"  # OK | CLARIFY | SAFE_FAILURE | PROVISIONAL
    diagnostics: dict[str, Any] = field(default_factory=dict)


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

        # 1. FAST extraction
        try:
            fast_result = await self._normalize_fast(
                await self._invoke_fast(utterance, locale=locale),
                utterance=utterance,
            )
            outcome.fast_success = True
            outcome.intent = (fast_result.get("intent") or {}).get("type")
            budget = fast_result.get("budget") or {}
            outcome.budget_value = _as_float(
                budget.get("value")
                if budget.get("value") is not None
                else budget.get("maximum")
            )
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

        # 2. Semantic category match
        category_code: Optional[str] = None
        try:
            match_result = await self._normalize_match(
                await self._invoke_matcher(utterance, locale=locale, fast_result=fast_result)
            )
            if match_result and match_result.get("status") == "MATCHED":
                outcome.category_id = match_result.get("category_id")
                outcome.category_name = match_result.get("category_name")
                category_code = match_result.get("category_code")
                outcome.category_code = category_code
                diag["match"] = {
                    "category_id": outcome.category_id,
                    "category_code": category_code,
                    "score": match_result.get("score"),
                    "method": match_result.get("method"),
                }
            else:
                diag["match"] = {"status": "NO_MATCH"}
        except Exception as exc:
            logger.exception("Semantic match failed for session %s", session_id)
            diag["match_error"] = str(exc)

        # 3. Early exit
        if not outcome.category_id and not outcome.budget_value:
            outcome.gate_status = "CLARIFY"
            outcome.diagnostics = diag
            return outcome

        # 4. Load candidates
        try:
            candidates = await self._load_candidates(
                category_id=outcome.category_id,
                category_code=category_code,
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
            return outcome

        # 5. Eligibility
        need_profile = {
            "budget": {
                "value": outcome.budget_value,
                "type": outcome.budget_type or "APPROXIMATE",
            },
            "intent": outcome.intent,
            "category_id": outcome.category_id,
        }
        try:
            eligible = await self._filter_eligible(
                candidates,
                need_profile,
                category_codes=[category_code] if category_code else [],
            )
        except Exception as exc:
            logger.exception("Eligibility failed")
            outcome.gate_status = "SAFE_FAILURE"
            outcome.diagnostics = {**diag, "error": "eligibility", "detail": str(exc)}
            return outcome
        diag["eligible"] = len(eligible)
        if not eligible:
            outcome.diagnostics = diag
            return outcome

        # 6. Ranking
        try:
            ranked = self._rank(eligible, need_profile, max_recommendations)
            outcome.ranked_campaigns = ranked
            diag["ranked"] = [
                {"id": c.get("id"), "score": c.get("score")} for c in ranked
            ]
        except Exception as exc:
            logger.exception("Ranking failed")
            outcome.gate_status = "SAFE_FAILURE"
            outcome.diagnostics = {**diag, "error": "ranking", "detail": str(exc)}
            return outcome

        # 7. Gate
        if self._gate is not None:
            gate_decision = self._gate.evaluate(
                ranked=outcome.ranked_campaigns,
                need_profile=need_profile,
                context={"session_id": session_id, "locale": locale},
            )
            outcome.gate_status = gate_decision.get("status", "OK")
            diag["gate"] = gate_decision
        else:
            top = outcome.ranked_campaigns[0].get("score", 0) if outcome.ranked_campaigns else 0
            outcome.gate_status = "PROVISIONAL" if top < 0.35 else "OK"

        outcome.diagnostics = diag
        return outcome

    # ------------------------------------------------------------------
    # Component adapters
    # ------------------------------------------------------------------

    async def _invoke_fast(self, utterance: str, *, locale: str) -> Any:
        extract = getattr(self._fast, "extract", None)
        if extract is None:
            raise RuntimeError("fast_extractor has no extract()")
        try:
            return await extract(utterance, locale=locale)
        except TypeError:
            return await extract(utterance)

    async def _normalize_fast(self, raw: Any, *, utterance: str) -> dict[str, Any]:
        if isinstance(raw, Mapping):
            data = dict(raw)
        else:
            profile = getattr(raw, "need_profile", None) or {}
            intent = profile.get("intent") if isinstance(profile, Mapping) else None
            if not isinstance(intent, Mapping):
                intent = {"type": intent} if intent else {"type": "UNKNOWN"}
            data = {
                "intent": intent,
                "budget": (profile.get("budget") if isinstance(profile, Mapping) else {})
                or {},
                "category_signals": {"positive": [], "negative": []},
            }

        budget = data.get("budget") or {}
        if not budget.get("value") and not budget.get("maximum"):
            # Production fallback: deterministic query parser (budget/intent).
            try:
                from taksitlio.query_understanding.fast_parser import fast_parse

                parsed = fast_parse(utterance).to_dict()
                pb = parsed.get("budget") or {}
                if pb.get("value") is not None or pb.get("maximum") is not None:
                    data["budget"] = pb
                if not (data.get("intent") or {}).get("type"):
                    data["intent"] = {"type": parsed.get("intent") or "PRODUCT_SEARCH"}
            except Exception:
                logger.debug("fast_parse budget fallback unavailable", exc_info=True)
        return data

    async def _invoke_matcher(
        self, utterance: str, *, locale: str, fast_result: Mapping[str, Any]
    ) -> Any:
        match = getattr(self._matcher, "match", None)
        if match is None:
            return {"status": "NO_MATCH"}
        signals = (fast_result.get("category_signals") or {}).get("positive") or []
        # Mock / guest dict API
        try:
            return await match(
                {
                    "utterance": utterance,
                    "locale": locale,
                    "signals": signals,
                }
            )
        except TypeError:
            pass
        # Real SemanticCategoryMatcher(need_description, extra_texts=...)
        return await match(utterance, extra_texts=[str(s) for s in signals if s])

    async def _normalize_match(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Mapping):
            return dict(raw)
        matches = getattr(raw, "matches", None) or []
        if not matches:
            return {"status": "NO_MATCH"}
        top = matches[0]
        category = getattr(top, "category", None)
        if category is None:
            return {"status": "NO_MATCH"}
        return {
            "status": "MATCHED",
            "category_id": getattr(category, "id", None),
            "category_name": getattr(category, "display_name", None),
            "category_code": getattr(category, "category_code", None),
            "score": getattr(top, "score", None),
            "method": "semantic_matcher",
        }

    async def _load_candidates(
        self,
        *,
        category_id: Optional[int],
        category_code: Optional[str],
        locale: str,
    ) -> list[Any]:
        if hasattr(self._campaigns, "list_active"):
            return list(
                await self._campaigns.list_active(
                    category_id=category_id, locale=locale
                )
            )
        if hasattr(self._campaigns, "list_by_category_codes") and category_code:
            return list(
                await self._campaigns.list_by_category_codes([category_code], limit=50)
            )
        if hasattr(self._campaigns, "list_by_category_codes"):
            # Broad fallback — all known seed codes if category unresolved
            return list(
                await self._campaigns.list_by_category_codes(
                    ["MOBILE_PHONE", "LAPTOP", "TABLET", "HOME_APPLIANCE"],
                    limit=50,
                )
            )
        return []

    async def _filter_eligible(
        self,
        candidates: Sequence[Any],
        need_profile: Mapping[str, Any],
        *,
        category_codes: Sequence[str],
    ) -> list[Any]:
        if hasattr(self._eligibility, "is_eligible"):
            out = []
            for camp in candidates:
                try:
                    if self._eligibility.is_eligible(camp, need_profile):
                        out.append(camp)
                except Exception:
                    continue
            return out

        if hasattr(self._eligibility, "filter_eligible"):
            rules = [{"type": "STATUS_ACTIVE"}, {"type": "WITHIN_DATE_WINDOW"}]
            if hasattr(self._campaigns, "get_eligibility_rules"):
                try:
                    rules = await self._campaigns.get_eligibility_rules()
                except Exception:
                    pass
            # Only Campaign dataclasses go through real eligibility
            from taksitlio.campaign.models import Campaign

            typed = [c for c in candidates if isinstance(c, Campaign)]
            if typed:
                return self._eligibility.filter_eligible(
                    typed,
                    need_profile,
                    category_codes=list(category_codes) or [
                        c.category_code for c in typed
                    ],
                    rules=rules,
                )
            return list(candidates)
        return list(candidates)

    def _rank(
        self,
        eligible: Sequence[Any],
        need_profile: Mapping[str, Any],
        max_results: int,
    ) -> list[dict[str, Any]]:
        # Prefer keyword API of RankingEngine
        try:
            ranked = self._ranker.rank(
                eligible,
                need_profile,
                weights=_GUEST_RANK_WEIGHTS,
                max_results=max_results,
            )
        except TypeError:
            ranked = self._ranker.rank(
                campaigns=eligible,
                need_profile=need_profile,
                max_results=max_results,
                weights=_GUEST_RANK_WEIGHTS,
            )

        out: list[dict[str, Any]] = []
        for item in ranked:
            if isinstance(item, Mapping):
                out.append(dict(item))
                continue
            camp = getattr(item, "campaign", None)
            score = float(getattr(item, "score", 0.0) or 0.0)
            if camp is None:
                continue
            out.append(
                {
                    "id": getattr(camp, "id", None),
                    "title": getattr(camp, "title", None),
                    "subtitle": getattr(camp, "summary", None),
                    "summary": getattr(camp, "summary", None),
                    "bank": getattr(camp, "brand", None),
                    "rate_text": (getattr(camp, "attributes", None) or {}).get(
                        "rate_text"
                    ),
                    "max_amount": getattr(camp, "max_budget", None),
                    "max_tenure": getattr(camp, "installment_count", None),
                    "text": getattr(camp, "search_text", None),
                    "score": score,
                    "list_price": getattr(camp, "list_price", None),
                }
            )
        return out


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

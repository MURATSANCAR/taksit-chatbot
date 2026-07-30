"""End-to-end chat pipeline orchestrator."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from taksitlio.campaign.eligibility import EligibilityEngine
from taksitlio.campaign.models import CampaignRetriever
from taksitlio.campaign.ranking import RankedCampaign, RankingEngine
from taksitlio.category.matcher import SemanticCategoryMatcher
from taksitlio.model_router.router import RouteDecision
from taksitlio.response.grounded import GroundedReply, GroundedResponseGenerator
from taksitlio.understanding.service import UnderstandingService, UnderstoodTurn


@dataclass(frozen=True)
class ChatRequest:
    session_id: str
    message: str
    user_id: str | None = None


@dataclass(frozen=True)
class ChatResponse:
    session_id: str
    reply: str
    decision: str
    need_profile: dict[str, Any] | None
    categories: list[dict[str, Any]] = field(default_factory=list)
    campaigns: list[dict[str, Any]] = field(default_factory=list)
    cta: dict[str, Any] | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0


class ChatPipeline:
    def __init__(
        self,
        understanding: UnderstandingService,
        category_matcher: SemanticCategoryMatcher,
        retriever: CampaignRetriever,
        eligibility: EligibilityEngine,
        ranking: RankingEngine,
        responder: GroundedResponseGenerator,
        *,
        campaign_repo: Any,
    ) -> None:
        self._understanding = understanding
        self._categories = category_matcher
        self._retriever = retriever
        self._eligibility = eligibility
        self._ranking = ranking
        self._responder = responder
        self._campaign_repo = campaign_repo

    async def handle(self, request: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        turn = await self._understanding.process_message(
            session_id=request.session_id,
            message=request.message,
            user_id=request.user_id,
        )

        understanding = turn.understanding
        need_profile = turn.need_profile

        if understanding.decision == RouteDecision.CLARIFY:
            reply = await self._responder.clarify(
                understanding.clarification_question_intent
            )
            return self._build(request, turn, reply, [], [], started)

        if need_profile is None:
            reply = await self._responder.out_of_scope()
            return self._build(
                request,
                turn,
                reply,
                [],
                [],
                started,
                extra={"reason": understanding.reason},
            )

        intent_type = (need_profile.get("intent") or {}).get("type")
        if intent_type == "OUT_OF_SCOPE":
            reply = await self._responder.out_of_scope()
            return self._build(request, turn, reply, [], [], started)

        # Category match
        usage = need_profile.get("usage_context") or []
        prefs = [
            str(p.get("concept") or "")
            for p in (need_profile.get("preferences") or [])
            if p.get("concept")
        ]
        match_result = await self._categories.match(
            str(need_profile.get("need_description") or request.message),
            extra_texts=[*usage, *prefs],
        )

        if match_result.needs_clarification and len(match_result.matches) != 1:
            reply = await self._responder.clarify("category")
            return self._build(
                request,
                turn,
                reply,
                match_result.matches,
                [],
                started,
                extra={
                    "category_score_gap": match_result.score_gap,
                    "category_candidates": [
                        {"code": m.category.category_code, "score": m.score}
                        for m in match_result.matches
                    ],
                },
            )

        category_codes = [m.category.category_code for m in match_result.matches]
        await self._understanding._sessions.apply_need_profile(
            request.session_id,
            need_profile,
            category_codes=category_codes,
        )

        # Retrieve → eligibility → rank
        candidates = await self._retriever.retrieve(category_codes)
        rules = await self._campaign_repo.get_eligibility_rules()
        eligible = self._eligibility.filter_eligible(
            candidates,
            need_profile,
            category_codes=category_codes,
            rules=rules,
        )
        ranking_policy = await self._campaign_repo.get_ranking_policy()
        ranked = self._ranking.rank(
            eligible,
            need_profile,
            weights=ranking_policy.get("weights") or {},
            max_results=int(ranking_policy.get("max_results") or 5),
            query_embedding=match_result.query_embedding,
        )

        reply = await self._responder.from_campaigns(need_profile, ranked)
        return self._build(
            request,
            turn,
            reply,
            match_result.matches,
            ranked,
            started,
            extra={
                "retrieved": len(candidates),
                "eligible": len(eligible),
                "ranked": len(ranked),
                "model_profile": understanding.used_profile_code,
                "fallback_used": understanding.fallback_used,
                "was_update": turn.was_update,
            },
        )

    def _build(
        self,
        request: ChatRequest,
        turn: UnderstoodTurn,
        reply: GroundedReply,
        matches: list[Any],
        ranked: list[RankedCampaign] | list[Any],
        started: float,
        *,
        extra: dict[str, Any] | None = None,
    ) -> ChatResponse:
        categories = []
        for m in matches:
            if hasattr(m, "category"):
                categories.append(
                    {
                        "category_code": m.category.category_code,
                        "display_name": m.category.display_name,
                        "score": m.score,
                    }
                )
        campaigns = reply.campaigns
        if not campaigns and ranked:
            campaigns = [
                {
                    **r.campaign.to_grounding_dict(),
                    "rank_score": r.score,
                    "rank_components": r.components,
                }
                for r in ranked
                if hasattr(r, "campaign")
            ]
        cta = None
        if reply.cta:
            cta = {
                "enabled": reply.cta.enabled,
                "label": reply.cta.label,
                "url": reply.cta.url,
                "reason": reply.cta.reason,
            }
        return ChatResponse(
            session_id=request.session_id,
            reply=reply.text,
            decision=turn.understanding.decision.value,
            need_profile=turn.need_profile,
            categories=categories,
            campaigns=campaigns,
            cta=cta,
            diagnostics={
                "understanding_reason": turn.understanding.reason,
                "understanding_latency_ms": turn.understanding.latency_ms,
                "reply_template": reply.template_used,
                **(extra or {}),
            },
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )

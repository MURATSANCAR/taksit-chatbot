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
from taksitlio.product_query.chat_bridge import (
    ProductPathDeps,
    need_profile_to_search_request,
    run_catalog_search_for_chat,
)
from taksitlio.response.grounded import GroundedReply, GroundedResponseGenerator
from taksitlio.search_sessions.chat_bridge import bridge_search_start
from taksitlio.search_sessions.orchestrator import SearchOrchestrator
from taksitlio.semantic_matching.query_intent import is_off_domain_for_assist
from taksitlio.understanding.service import UnderstandingService, UnderstoodTurn


@dataclass(frozen=True)
class ChatRequest:
    session_id: str
    message: str
    user_id: str | None = None
    product_phase: str | None = None  # FIRST_CARDS | FINANCE_ENRICHED
    prefer_search_sessions: bool = True


@dataclass(frozen=True)
class ChatResponse:
    session_id: str
    reply: str
    decision: str
    need_profile: dict[str, Any] | None
    categories: list[dict[str, Any]] = field(default_factory=list)
    campaigns: list[dict[str, Any]] = field(default_factory=list)
    cards: list[dict[str, Any]] = field(default_factory=list)
    phase: str | None = None
    cta: dict[str, Any] | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    search_session_id: str | None = None
    events_url: str | None = None
    clarification: dict[str, Any] | None = None
    chips: list[dict[str, Any]] = field(default_factory=list)


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
        product_path: ProductPathDeps | None = None,
        search_orchestrator: SearchOrchestrator | None = None,
    ) -> None:
        self._understanding = understanding
        self._categories = category_matcher
        self._retriever = retriever
        self._eligibility = eligibility
        self._ranking = ranking
        self._responder = responder
        self._campaign_repo = campaign_repo
        self._product_path = product_path
        self._search_orchestrator = search_orchestrator

    async def handle(self, request: ChatRequest) -> ChatResponse:
        started = time.perf_counter()

        # Hard gate: no general chat / no inventing off-catalog knowledge.
        if is_off_domain_for_assist(request.message):
            reply = await self._responder.out_of_scope()
            return ChatResponse(
                session_id=request.session_id,
                reply=reply.text,
                decision="SAFE_FAILURE",
                need_profile=None,
                diagnostics={
                    "off_domain": True,
                    "reply_template": reply.template_used,
                },
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )

        # ADR-011: clarification-first product search before legacy understanding LLM.
        # Explicit product_phase keeps ADR-010 catalog progressive card path.
        if (
            request.prefer_search_sessions
            and self._search_orchestrator is not None
            and not request.product_phase
            and self._looks_like_product_query(request.message)
        ):
            await self._refresh_search_catalog(request.message)
            bridged = bridge_search_start(
                self._search_orchestrator,
                conversation_id=conversation_id_for_session(request.session_id),
                message=request.message,
                user_id=request.user_id,
            )
            return ChatResponse(
                session_id=request.session_id,
                reply=bridged.reply,
                decision=bridged.decision,
                need_profile=bridged.need_profile,
                cards=bridged.cards,
                phase=bridged.phase,
                diagnostics=bridged.diagnostics,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                search_session_id=bridged.diagnostics.get("search_session_id"),
                events_url=bridged.diagnostics.get("events_url"),
                clarification=bridged.diagnostics.get("clarification"),
                chips=list(bridged.diagnostics.get("chips") or []),
            )

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
                extra={
                    "reason": understanding.reason_code.value
                    if hasattr(understanding, "reason_code")
                    else getattr(understanding, "reason", None)
                },
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
        sessions = getattr(self._understanding, "sessions", None)
        if sessions is not None:
            await sessions.apply_need_profile(
                request.session_id,
                need_profile,
                category_codes=category_codes,
            )
        else:
            await self._understanding._sessions.apply_need_profile(
                request.session_id,
                need_profile,
                category_codes=category_codes,
            )

        # Prefer ADR-010 catalog path when products exist; else legacy V004 campaigns.
        product_hit = await self._try_product_path(request, need_profile)
        if product_hit is not None:
            reply, cards, phase, product_extra = product_hit
            return self._build(
                request,
                turn,
                reply,
                match_result.matches,
                [],
                started,
                cards=cards,
                phase=phase,
                extra={
                    "product_path": True,
                    "model_profile": understanding.used_profile_code,
                    "fallback_used": understanding.fallback_used,
                    "was_update": turn.was_update,
                    **product_extra,
                },
            )

        # Retrieve → eligibility → rank (legacy campaigns)
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
                "product_path": False,
                "retrieved": len(candidates),
                "eligible": len(eligible),
                "ranked": len(ranked),
                "model_profile": understanding.used_profile_code,
                "fallback_used": understanding.fallback_used,
                "was_update": turn.was_update,
            },
        )

    async def _try_product_path(
        self,
        request: ChatRequest,
        need_profile: dict[str, Any],
    ) -> tuple[GroundedReply, list[dict[str, Any]], str, dict[str, Any]] | None:
        if self._product_path is None or not self._product_path.enabled:
            return None
        phase = (request.product_phase or "FINANCE_ENRICHED").upper()
        search_req = need_profile_to_search_request(
            request.message, need_profile, phase=phase
        )
        result = await run_catalog_search_for_chat(self._product_path, search_req)
        if result is None:
            return None
        # Empty cards with no clarifications → fall back to legacy campaigns.
        if not result.cards and not result.search.clarifications:
            return None
        reply = await self._responder.from_product_cards(
            need_profile,
            phase=result.phase,
            cards=result.cards,
            clarifications=result.search.clarifications,
        )
        extra = {
            "catalog_phase": result.phase,
            "card_count": len(result.cards),
            "clarifications": list(result.search.clarifications),
            "ranking_mode": search_req.ranking_mode.value,
            "refresh_jobs": len(result.search.refresh_jobs),
        }
        return reply, list(result.cards), result.phase, extra

    def _build(
        self,
        request: ChatRequest,
        turn: UnderstoodTurn,
        reply: GroundedReply,
        matches: list[Any],
        ranked: list[RankedCampaign] | list[Any],
        started: float,
        *,
        cards: list[dict[str, Any]] | None = None,
        phase: str | None = None,
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
        out_cards = list(cards if cards is not None else reply.cards)
        out_phase = phase if phase is not None else reply.phase
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
            cards=out_cards,
            phase=out_phase,
            cta=cta,
            diagnostics={
                "understanding_reason": turn.understanding.reason_code.value
                if hasattr(turn.understanding, "reason_code")
                else getattr(turn.understanding, "reason", None),
                "understanding_latency_ms": turn.understanding.latency_ms,
                "system_confidence": getattr(
                    turn.understanding, "system_confidence", None
                ),
                "reply_template": reply.template_used,
                **(extra or {}),
            },
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )

    @staticmethod
    def _looks_like_product_query(message: str) -> bool:
        if is_off_domain_for_assist(message):
            return False
        lower = (message or "").casefold()
        cues = (
            "alacağım",
            "almak",
            "arıyorum",
            "istiyorum",
            "bakıyorum",
            "laptop",
            "telefon",
            "tablet",
            "televizyon",
            "bilgisayar",
            "cihaz",
            "ürün",
            "taksit",
            "bütçe",
            "bin",
            "tl",
            "ay ",
            "apple",
            "samsung",
            "macbook",
            "iphone",
            "buzdolab",
            "beyaz eşya",
            "çamaşır",
            "camasir",
            "bulaşık",
            "bulasik",
            "klima",
            "mobilya",
        )
        return any(c in lower for c in cues)

    async def _refresh_search_catalog(self, utterance: str) -> None:
        """Reload product pool + category hints from live catalog before search."""

        orch = self._search_orchestrator
        path = self._product_path
        if orch is None or path is None or path.catalog is None:
            return
        from taksitlio.search_sessions.catalog_pool import refresh_orchestrator_from_catalog

        category_source = getattr(self._categories, "_repo", None)
        await refresh_orchestrator_from_catalog(
            orch,
            catalog=path.catalog,
            merchants=path.merchant_directory,
            finance_index=path.finance_index,
            institutions=path.institution_labels,
            logos=getattr(orch, "logo_resolver", None),
            categories=category_source,
            utterance=utterance,
        )


def _is_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def conversation_id_for_session(session_id: str) -> str:
    """Stable UUID for search_sessions.conversation_id from opaque chat session ids."""

    import uuid

    if _is_uuid(session_id):
        return str(uuid.UUID(session_id))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"taksitlio:chat:{session_id}"))

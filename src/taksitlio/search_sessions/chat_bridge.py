"""Map search_session orchestrator results into chat-facing payloads (ADR-011 P1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from taksitlio.search_sessions.orchestrator import SearchOrchestrator


@dataclass(frozen=True)
class SearchChatBridgeResult:
    reply: str
    decision: str
    cards: list[dict[str, Any]]
    phase: Optional[str]
    need_profile: Optional[dict[str, Any]]
    diagnostics: dict[str, Any]


def _cards_from_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    snap = payload.get("results") or payload.get("partial_results") or {}
    products = snap.get("products") or []
    cards: list[dict[str, Any]] = []
    for p in products:
        thumb = p.get("thumbnail_cdn_url")
        cards.append(
            {
                "product_id": p.get("product_id"),
                "display_name": p.get("display_name"),
                "merchant": {"display_name": p.get("merchant_display_name")},
                "price": p.get("price"),
                "currency": "TRY",
                "stock_status": "AVAILABLE",
                "ranking_label": snap.get("label") or "Ön sonuçlar",
                "image": {
                    "status": "READY" if thumb else "IMAGE_UNAVAILABLE",
                    "thumbnail_cdn_url": thumb,
                },
                "best_finance": p.get("best_finance_summary"),
            }
        )
    return cards


def _reply_for_route(payload: dict[str, Any]) -> tuple[str, str]:
    route = payload.get("route") or "FAST"
    if route == "OUT_OF_SCOPE":
        from taksitlio.semantic_matching.query_intent import OUT_OF_SCOPE_ASSIST_MESSAGE

        return (
            payload.get("reply") or OUT_OF_SCOPE_ASSIST_MESSAGE,
            "SAFE_FAILURE",
        )
    if route == "CLARIFICATION":
        clar = payload.get("clarification") or {}
        q = clar.get("question_text") or "Bir tercihinizi netleştirebilir misiniz?"
        return q, "CLARIFY"
    if route == "LLM":
        return (
            "Tercihlerinizi ürün özellikleriyle eşleştiriyorum. "
            "İlk uygun ürünleri gösteriyorum.",
            "CONTINUE",
        )
    if route == "DEGRADED":
        return (
            "Elimdeki kesin kriterlere göre en yakın sonuçları hazırladım. "
            "Bir tercih daha ekleyerek sonuçları daraltabilirsiniz.",
            "CONTINUE",
        )
    count = len((payload.get("results") or {}).get("products") or [])
    if count:
        return f"Kriterlerinize uygun {count} ürün buldum.", "CONTINUE"
    return "Kriterlerinize göre ürün araması tamamlandı.", "CONTINUE"


def bridge_search_start(
    orch: SearchOrchestrator,
    *,
    conversation_id: str,
    message: str,
    user_id: Optional[str] = None,
) -> SearchChatBridgeResult:
    payload = orch.start(
        conversation_id=conversation_id,
        message=message,
        user_id=user_id,
    )
    reply, decision = _reply_for_route(payload)
    cards = _cards_from_results(payload)
    phase = None
    if cards:
        phase = "PARTIAL" if payload.get("route") == "LLM" else "FIRST_CARDS"
    if payload.get("route") == "CLARIFICATION":
        phase = "CLARIFICATION"
    return SearchChatBridgeResult(
        reply=reply,
        decision=decision,
        cards=cards,
        phase=phase,
        need_profile=payload.get("understanding"),
        diagnostics={
            "search_path": True,
            "search_session_id": payload.get("search_session_id"),
            "query_version": payload.get("query_version"),
            "events_url": payload.get("events_url"),
            "route": payload.get("route"),
            "clarification": payload.get("clarification"),
            "chips": payload.get("chips") or [],
            "logos": payload.get("logos") or {},
            "controls": payload.get("controls"),
            "llm_job_id": payload.get("llm_job_id"),
            "platform_role": payload.get("platform_role"),
            "partial_results": payload.get("partial_results"),
        },
    )


def bridge_clarification_answer(
    orch: SearchOrchestrator,
    *,
    search_session_id: str,
    clarification_id: str,
    selected_option_ids: list[str],
    expected_query_version: int,
    free_text: Optional[str] = None,
) -> SearchChatBridgeResult:
    payload = orch.answer_clarification(
        search_session_id,
        clarification_id=clarification_id,
        selected_option_ids=selected_option_ids,
        free_text=free_text,
        expected_query_version=expected_query_version,
    )
    reply, decision = _reply_for_route(payload)
    cards = _cards_from_results(payload)
    return SearchChatBridgeResult(
        reply=reply,
        decision=decision,
        cards=cards,
        phase="FIRST_CARDS" if cards else payload.get("route"),
        need_profile=payload.get("understanding"),
        diagnostics={
            "search_path": True,
            "search_session_id": payload.get("search_session_id"),
            "query_version": payload.get("query_version"),
            "route": payload.get("route"),
            "clarification": payload.get("clarification"),
            "chips": payload.get("chips") or [],
            "logos": payload.get("logos") or {},
        },
    )

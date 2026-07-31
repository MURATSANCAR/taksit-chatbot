from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from taksitlio.api.deps import container_from
from taksitlio.pipeline.orchestrator import ChatRequest

router = APIRouter(tags=["chat"])


class ChatMessageIn(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    message: str = Field(..., min_length=1, max_length=4000)
    user_id: Optional[str] = Field(default=None, max_length=128)
    product_phase: Optional[str] = Field(
        default=None,
        description="FIRST_CARDS | FINANCE_ENRICHED — progressive catalog phase",
    )


class ChatMessageOut(BaseModel):
    session_id: str
    reply: str
    decision: str
    need_profile: Optional[Dict[str, Any]] = None
    categories: List[Dict[str, Any]] = Field(default_factory=list)
    campaigns: List[Dict[str, Any]] = Field(default_factory=list)
    cards: List[Dict[str, Any]] = Field(default_factory=list)
    phase: Optional[str] = None
    cta: Optional[Dict[str, Any]] = None
    diagnostics: Dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0.0
    search_session_id: Optional[str] = None
    events_url: Optional[str] = None
    clarification: Optional[Dict[str, Any]] = None
    chips: List[Dict[str, Any]] = Field(default_factory=list)


@router.post("/chat", response_model=ChatMessageOut)
async def chat(payload: ChatMessageIn, request: Request) -> ChatMessageOut:
    container = container_from(request)
    try:
        result = await container.pipeline.handle(
            ChatRequest(
                session_id=payload.session_id,
                message=payload.message,
                user_id=payload.user_id,
                product_phase=payload.product_phase,
            )
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ChatMessageOut(
        session_id=result.session_id,
        reply=result.reply,
        decision=result.decision,
        need_profile=result.need_profile,
        categories=result.categories,
        campaigns=result.campaigns,
        cards=result.cards,
        phase=result.phase,
        cta=result.cta,
        diagnostics=result.diagnostics,
        latency_ms=result.latency_ms,
        search_session_id=result.search_session_id,
        events_url=result.events_url,
        clarification=result.clarification,
        chips=result.chips,
    )


@router.delete("/sessions/{session_id}")
async def reset_session(session_id: str, request: Request) -> Dict[str, str]:
    container = container_from(request)
    sessions = container.extras.get("sessions")
    if sessions is None:
        raise HTTPException(status_code=501, detail="Session manager unavailable")
    await sessions.reset(session_id)
    return {"status": "reset", "session_id": session_id}

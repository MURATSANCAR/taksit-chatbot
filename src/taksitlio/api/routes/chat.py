from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from taksitlio.api.deps import container_from
from taksitlio.application.guest_orchestrator_adapter import GuestOrchestratorAdapter
from taksitlio.llm_routing.worker import schedule_llm_job
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
    revision: Optional[int] = Field(default=None, ge=0)
    client_message_id: Optional[str] = Field(default=None, max_length=128)
    client_sequence: Optional[int] = Field(default=None, ge=1)


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
    revision: Optional[int] = None


@router.post("/chat", response_model=ChatMessageOut)
async def chat(payload: ChatMessageIn, request: Request) -> ChatMessageOut:
    container = container_from(request)

    # ========== GUEST (loginsiz) BRANCH ==========
    if not payload.user_id:
        try:
            adapter = GuestOrchestratorAdapter.from_container(container)

            msg = (payload.message or "").strip().lower()
            is_opening = (
                not payload.session_id
                or payload.session_id in ("new", "null", "")
                or msg in ("", "merhaba", "selam", "hi", "hello")
            )

            if is_opening and msg in ("", "merhaba", "selam", "hi", "hello"):
                result = await adapter.start_guest_session(locale="tr-TR")
            else:
                # Session_id yoksa önce açılış yapıp id al
                session_id = payload.session_id
                if not session_id or session_id in ("new", "null", ""):
                    open_res = await adapter.start_guest_session(locale="tr-TR")
                    session_id = open_res["session_id"]

                expected_revision = payload.revision or 0
                # Prefer explicit client_sequence; otherwise derive from revision so
                # multi-turn curl/smokes without sequence don't hit CAS duplicates.
                client_sequence = payload.client_sequence
                if client_sequence is None and expected_revision:
                    client_sequence = int(expected_revision) + 1
                result = await adapter.handle_guest_turn(
                    session_id=session_id,
                    utterance=payload.message,
                    expected_revision=expected_revision,
                    client_message_id=payload.client_message_id or str(uuid.uuid4()),
                    client_sequence=client_sequence,
                    locale="tr-TR",
                )

            return _map_guest_to_out(result)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    # ========== /GUEST BRANCH ==========

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
    schedule_llm_job(
        container.extras.get("llm_understanding_worker"),
        (result.diagnostics or {}).get("llm_job_id"),
    )
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


def _map_guest_to_out(result: dict) -> ChatMessageOut:
    messages = result.get("messages") or []
    text_parts = [m.get("content", "") for m in messages if m.get("type") == "text"]
    reply = "\n\n".join(p for p in text_parts if p).strip()

    cards = [
        m["card"]
        for m in messages
        if m.get("type") == "campaign_card" and m.get("card")
    ]

    phase = result.get("phase", "COMPLETED")
    cta = result.get("membership_cta")

    if cards:
        decision = "GUEST_RECOMMENDATION"
    elif phase == "CLARIFY":
        decision = "GUEST_CLARIFY"
    else:
        decision = "GUEST_SAFE"

    return ChatMessageOut(
        session_id=result["session_id"],
        reply=reply,
        decision=decision,
        need_profile=result.get("diagnostics") or {},
        categories=[],
        campaigns=cards,
        cards=cards,
        phase=phase,
        cta=cta,
        diagnostics=result.get("diagnostics") or {},
        latency_ms=0.0,
        search_session_id=None,
        events_url=None,
        clarification={"text": reply} if phase == "CLARIFY" else None,
        chips=[],
        revision=result.get("revision"),
    )


@router.delete("/sessions/{session_id}")
async def reset_session(session_id: str, request: Request) -> Dict[str, str]:
    container = container_from(request)
    sessions = container.extras.get("sessions")
    if sessions is None:
        raise HTTPException(status_code=501, detail="Session manager unavailable")
    await sessions.reset(session_id)
    return {"status": "reset", "session_id": session_id}

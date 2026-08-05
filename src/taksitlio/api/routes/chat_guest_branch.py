"""
DROP-IN: replace GUEST branch inside chat.py with this logic.

Keeps existing ChatMessageIn / ChatMessageOut / authenticated path.
Guest → CampaignOnly via GuestOrchestratorAdapter (campaign-only implementation).
cards always [].
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional


def map_guest_to_out(result: dict) -> dict:
    """Build ChatMessageOut fields from campaign-only pipeline result."""
    messages = result.get("messages") or []
    reply = result.get("reply") or ""
    if not reply:
        text_parts = [m.get("content", "") for m in messages if m.get("type") == "text"]
        reply = "\n\n".join(p for p in text_parts if p).strip()

    # Prefer explicit campaigns; never take product-shaped cards
    campaigns = list(result.get("campaigns") or [])
    if not campaigns:
        for m in messages:
            if m.get("type") == "campaign_card" and m.get("card"):
                campaigns.append(m["card"])

    phase = result.get("phase", "COMPLETED")
    cta = result.get("cta") or result.get("membership_cta")

    if campaigns:
        decision = "GUEST_RECOMMENDATION"
    elif phase == "CLARIFY":
        decision = "GUEST_CLARIFY"
    elif phase == "OPENING":
        decision = "GUEST_OPENING"
    else:
        decision = result.get("decision") or "GUEST_SAFE"

    return {
        "session_id": result["session_id"],
        "reply": reply,
        "decision": decision,
        "need_profile": result.get("diagnostics") or {},
        "categories": [],
        "campaigns": campaigns,
        "cards": [],  # HARD no product cards for guest
        "phase": phase,
        "cta": cta,
        "diagnostics": {
            **(result.get("diagnostics") or {}),
            "product_path": False,
            "campaign_only": True,
        },
        "latency_ms": 0.0,
        "search_session_id": None,
        "events_url": None,
        "clarification": {"text": reply} if phase == "CLARIFY" else None,
        "chips": [],
        "revision": result.get("revision"),
    }


async def run_guest_branch(payload: Any, container: Any) -> dict:
    """
    Full guest handling. Call from chat():

        if not payload.user_id:
            from taksitlio.api.routes.chat_guest_branch import run_guest_branch, map_guest_to_out
            raw = await run_guest_branch(payload, container)
            return ChatMessageOut(**map_guest_to_out(raw))
    """
    from taksitlio.application.guest_orchestrator_adapter import GuestOrchestratorAdapter

    adapter = GuestOrchestratorAdapter.from_container(container)

    msg = (payload.message or "").strip().lower()
    is_hello = msg in ("", "merhaba", "selam", "hi", "hello")
    sid = getattr(payload, "session_id", None)
    revision = getattr(payload, "revision", None)
    client_message_id = getattr(payload, "client_message_id", None) or str(uuid.uuid4())
    # Do NOT map revision → client_sequence (CAS treats equal seq as out-of-order).
    client_sequence = getattr(payload, "client_sequence", None)

    # Pure greeting with no prior revision → opening only
    if is_hello and revision in (None, 0):
        return await adapter.start_guest_session(locale="tr-TR", session_id=sid)

    expected_revision = int(revision or 0)
    if not sid or sid in ("new", "null", ""):
        open_res = await adapter.start_guest_session(locale="tr-TR", session_id=sid)
        sid = open_res["session_id"]
        if revision is None:
            expected_revision = int(open_res.get("revision") or 1)
        if not is_hello and (payload.message or "").strip():
            return await adapter.handle_guest_turn(
                session_id=sid,
                utterance=payload.message,
                expected_revision=expected_revision,
                client_message_id=client_message_id,
                client_sequence=client_sequence,
                locale="tr-TR",
            )
        return open_res

    # Client UUID may be new; pipeline creates under that id if missing.
    return await adapter.handle_guest_turn(
        session_id=sid,
        utterance=payload.message,
        expected_revision=expected_revision,
        client_message_id=client_message_id,
        client_sequence=client_sequence,
        locale="tr-TR",
    )


# ---------------------------------------------------------------------------
# Exact snippet to paste into chat.py (reference)
# ---------------------------------------------------------------------------
CHAT_PY_GUEST_SNIPPET = '''
    # ========== GUEST (loginsiz) CAMPAIGN-ONLY ==========
    if not payload.user_id:
        try:
            from taksitlio.api.routes.chat_guest_branch import (
                run_guest_branch,
                map_guest_to_out,
            )
            raw = await run_guest_branch(payload, container)
            out = map_guest_to_out(raw)
            return ChatMessageOut(**out)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    # ========== /GUEST ==========
'''

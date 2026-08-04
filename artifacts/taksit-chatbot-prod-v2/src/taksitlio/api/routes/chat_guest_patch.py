"""
Production GUEST branch for POST /chat.

Bu dosya mevcut `src/taksitlio/api/routes/chat.py` içine
minimal ve güvenli şekilde entegre edilecek kod parçalarını içerir.

Entegrasyon adımları (README_GUEST_INTEGRATION.md'de detaylı):
1. Import'ları ekle
2. ChatMessageIn'e opsiyonel alanlar (zaten var)
3. chat() fonksiyonunun başına GUEST branch koy
4. Mevcut pipeline.handle yolunu else'te bırak

Çakışma notu:
- Mevcut MembershipCTA / extract zaten varsa, guest handler
  sadece loginsiz (user_id is None) trafiği yakalar.
- Authenticated path hiç değişmez.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 1. Yeni import'lar (mevcut import'ların yanına ekle)
# ---------------------------------------------------------------------------
# from typing import Optional
# from fastapi import Request, HTTPException
# from taksitlio.api.deps import container_from
# from taksitlio.application.guest_orchestrator_adapter import GuestOrchestratorAdapter
# from taksitlio.guest.entry import GuestPhase

# ---------------------------------------------------------------------------
# 2. GUEST branch – chat() fonksiyonunun en başına ekle
# ---------------------------------------------------------------------------
#
# async def chat(payload: ChatMessageIn, request: Request) -> ChatMessageOut:
#     container = container_from(request)
#
#     # ---------- GUEST (loginsiz) branch ----------
#     if not payload.user_id:
#         adapter = GuestOrchestratorAdapter.from_container(container)
#
#         # İlk mesaj / session yoksa proaktif açılış
#         # Client session_id göndermiyorsa veya "new" gönderiyorsa start_session
#         is_opening = (
#             not payload.session_id
#             or payload.session_id in ("new", "null", "")
#             or (payload.message or "").strip().lower() in ("", "merhaba", "selam", "hi")
#         )
#
#         if is_opening and (not payload.message or payload.message.strip().lower() in ("", "merhaba", "selam", "hi")):
#             result = await adapter.start_guest_session(locale="tr-TR")
#         else:
#             # Normal free-text turn
#             # revision client'tan gelmiyorsa 0 kabul et (CAS ilk yazım)
#             expected_revision = getattr(payload, "revision", 0) or 0
#             result = await adapter.handle_guest_turn(
#                 session_id=payload.session_id or result.get("session_id"),  # start sonrası
#                 utterance=payload.message,
#                 expected_revision=expected_revision,
#                 client_message_id=getattr(payload, "client_message_id", None) or str(uuid.uuid4()),
#                 client_sequence=getattr(payload, "client_sequence", 1) or 1,
#                 locale="tr-TR",
#             )
#
#         return _guest_result_to_chat_message_out(result)
#
#     # ---------- Mevcut authenticated / pipeline yolu (değişmeden) ----------
#     ...


# ---------------------------------------------------------------------------
# 3. Helper: GuestTurnResult → mevcut ChatMessageOut
# ---------------------------------------------------------------------------

def _guest_result_to_chat_message_out(result: dict) -> "ChatMessageOut":
    """
    Guest adapter çıktısını mevcut response modeline map eder.
    Böylece mobil client tarafında hiçbir breaking change olmaz.
    """
    from taksitlio.api.routes.chat import ChatMessageOut  # mevcut model

    messages = result.get("messages") or []
    text_parts = [m.get("content", "") for m in messages if m.get("type") == "text"]
    reply = "\n\n".join(text_parts).strip()

    cards = []
    for m in messages:
        if m.get("type") == "campaign_card" and m.get("card"):
            cards.append(m["card"])

    cta = result.get("membership_cta")
    phase = result.get("phase", "COMPLETED")

    return ChatMessageOut(
        session_id=result["session_id"],
        reply=reply,
        decision="GUEST_RECOMMENDATION" if cards else "GUEST_CLARIFY",
        need_profile=result.get("diagnostics", {}),
        categories=[],
        campaigns=cards,
        cards=cards,
        phase=phase,
        cta=cta,
        diagnostics=result.get("diagnostics"),
        latency_ms=None,
        search_session_id=None,
        events_url=None,
        clarification=None if phase != "CLARIFY" else {"text": reply},
        chips=None,
    )

"""Production guest (loginsiz) entry handler — real ConversationStateManager API.

Uyumlu imzalar (mevcut manager.py):
  create_session(*, locale, actor=Actor(ANONYMOUS), session_id=None, ...)
      → ConversationState
  get_session(session_id: UUID) → ConversationState
  apply_model_update(session_id: UUID, *, expected_revision, patch, idempotency_key,
                     client_message_id, client_sequence=None) → CompareAndSetResult

Guest phase / recommendation snapshot → /resolved_context/guest (allowlisted path).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Sequence
from uuid import UUID

logger = logging.getLogger(__name__)


class GuestPhase(str, Enum):
    OPENING = "OPENING"
    AWAITING_NEED = "AWAITING_NEED"
    RECOMMENDING = "RECOMMENDING"
    COMPLETED = "COMPLETED"
    CLARIFY = "CLARIFY"
    SAFE_FAILURE = "SAFE_FAILURE"


@dataclass(frozen=True)
class GuestTurnResult:
    session_id: str
    phase: GuestPhase
    messages: Sequence[dict[str, Any]]
    state_revision: int
    diagnostics: dict[str, Any] = field(default_factory=dict)
    membership_cta: Optional[dict[str, Any]] = None

    def to_api_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "session_id": self.session_id,
            "phase": self.phase.value,
            "messages": list(self.messages),
            "revision": self.state_revision,
        }
        if self.membership_cta:
            payload["membership_cta"] = self.membership_cta
        if self.diagnostics:
            payload["diagnostics"] = self.diagnostics
        return payload


DEFAULT_OPENING = (
    "Merhaba! 👋 Taksitlio'ya hoş geldin.\n\n"
    "Senin için ihtiyaç analizi yapayım mı? "
    "Ne almak istediğini ve bütçeni kısaca yazman yeterli "
    "(örnek: \"cep telefonu alacağım, bütçem 40 bin TL civarı\")."
)

DEFAULT_CLARIFY_BUDGET = (
    "Bütçeni de belirtirsen sana en uygun 1-2 kampanyayı hemen bulabilirim. "
    "Yaklaşık tutarı yazman yeterli (örnek: 40.000 TL)."
)

DEFAULT_CLARIFY_CATEGORY = (
    "Hangi ürün kategorisinde bakıyorsun? "
    "(Cep telefonu, bilgisayar, beyaz eşya, tablet…)"
)

DEFAULT_NO_CAMPAIGN = (
    "Şu an bütçene ve ihtiyacına uygun aktif bir kampanya bulamadım. "
    "Üye olursan anlık kampanyaları görebilir ve başvurabilirsin."
)


class GuestEntryHandler:
    """
    Loginsiz (ANONYMOUS) session entry + needs-analysis → ranking → CTA.

    Tüm state yazımları ConversationStateManager üzerinden (CAS).
    Guest bilgisi /resolved_context/guest path'ine yazılır (allowlisted).
    """

    def __init__(
        self,
        state_manager: Any,
        needs_service: Any,
        *,
        opening_message: str = DEFAULT_OPENING,
        max_recommendations: int = 2,
        membership_cta_enabled: bool = True,
    ) -> None:
        self._state = state_manager
        self._needs = needs_service
        self._opening = opening_message
        self._max_recs = max_recommendations
        self._cta_enabled = membership_cta_enabled

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start_session(
        self,
        *,
        client_message_id: Optional[str] = None,
        locale: str = "tr-TR",
    ) -> GuestTurnResult:
        """Yeni ANONYMOUS session oluştur + proaktif açılış mesajı döndür."""
        from taksitlio.conversation_state.domain import Actor, ActorType

        state = await self._state.create_session(
            locale=locale,
            actor=Actor(type=ActorType.ANONYMOUS),
            idempotency_key=client_message_id or str(uuid.uuid4()),
        )

        # Phase'i resolved_context'e yaz (allowlisted path)
        cas = await self._state.apply_model_update(
            state.session_id,
            expected_revision=state.revision,
            patch={
                "operation": "SET",
                "path": "/resolved_context/guest",
                "value": {
                    "phase": GuestPhase.OPENING.value,
                    "entry_point": "loginsiz_chatbot",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            },
            idempotency_key=str(uuid.uuid4()),
            client_message_id=client_message_id or str(uuid.uuid4()),
            client_sequence=0,
        )

        return GuestTurnResult(
            session_id=str(state.session_id),
            phase=GuestPhase.OPENING,
            messages=[
                {
                    "role": "assistant",
                    "content": self._opening,
                    "type": "text",
                }
            ],
            state_revision=cas.revision if hasattr(cas, "revision") else state.revision + 1,
            diagnostics={"entry": "proactive_opening"},
        )

    async def handle_turn(
        self,
        session_id: str,
        user_utterance: str,
        *,
        expected_revision: int,
        client_message_id: str,
        client_sequence: int,
        locale: str = "tr-TR",
    ) -> GuestTurnResult:
        """Serbest metin turn → needs analysis → ranking → CTA."""
        sid = UUID(session_id) if not isinstance(session_id, UUID) else session_id

        try:
            state = await self._state.get_session(sid)
        except Exception as exc:
            logger.warning("Guest session not found/expired: %s (%s)", session_id, exc)
            # Session yoksa yeni aç ve açılış döndür
            return await self.start_session(
                client_message_id=client_message_id,
                locale=locale,
            )

        current_guest = (state.resolved_context or {}).get("guest") or {}
        current_phase = current_guest.get("phase", GuestPhase.AWAITING_NEED.value)

        # Needs-analysis pipeline
        outcome = await self._needs.analyse(
            utterance=user_utterance,
            session_id=str(sid),
            locale=locale,
            max_recommendations=self._max_recs,
        )

        diagnostics = {
            "phase_before": current_phase,
            "fast_ok": outcome.fast_success,
            "category_id": outcome.category_id,
            "budget_value": outcome.budget_value,
            "ranked_count": len(outcome.ranked_campaigns),
            "gate_status": outcome.gate_status,
        }

        # ---------- Decision tree ----------
        if outcome.gate_status == "SAFE_FAILURE":
            return await self._finalize(
                sid=sid,
                expected_revision=expected_revision,
                client_message_id=client_message_id,
                client_sequence=client_sequence,
                phase=GuestPhase.SAFE_FAILURE,
                messages=[
                    {
                        "role": "assistant",
                        "content": (
                            "Şu an senin için güvenli bir öneri üretemiyorum. "
                            "Lütfen daha sonra tekrar dene veya üye olarak devam et."
                        ),
                        "type": "text",
                    }
                ],
                diagnostics=diagnostics,
            )

        if not outcome.budget_value and not outcome.category_id:
            return await self._finalize(
                sid=sid,
                expected_revision=expected_revision,
                client_message_id=client_message_id,
                client_sequence=client_sequence,
                phase=GuestPhase.CLARIFY,
                messages=[
                    {
                        "role": "assistant",
                        "content": (
                            "Anladım. Hem kategoriyi hem de yaklaşık bütçeyi yazarsan "
                            "sana en uygun kampanyaları hemen gösterebilirim."
                        ),
                        "type": "text",
                    }
                ],
                diagnostics=diagnostics,
            )

        if not outcome.budget_value:
            return await self._finalize(
                sid=sid,
                expected_revision=expected_revision,
                client_message_id=client_message_id,
                client_sequence=client_sequence,
                phase=GuestPhase.CLARIFY,
                messages=[
                    {"role": "assistant", "content": DEFAULT_CLARIFY_BUDGET, "type": "text"}
                ],
                diagnostics=diagnostics,
            )

        if not outcome.category_id:
            return await self._finalize(
                sid=sid,
                expected_revision=expected_revision,
                client_message_id=client_message_id,
                client_sequence=client_sequence,
                phase=GuestPhase.CLARIFY,
                messages=[
                    {"role": "assistant", "content": DEFAULT_CLARIFY_CATEGORY, "type": "text"}
                ],
                diagnostics=diagnostics,
            )

        # Happy path
        if not outcome.ranked_campaigns:
            messages = [
                {
                    "role": "assistant",
                    "content": DEFAULT_NO_CAMPAIGN,
                    "type": "text",
                }
            ]
            cta = self._build_cta() if self._cta_enabled else None
            return await self._finalize(
                sid=sid,
                expected_revision=expected_revision,
                client_message_id=client_message_id,
                client_sequence=client_sequence,
                phase=GuestPhase.COMPLETED,
                messages=messages,
                membership_cta=cta,
                diagnostics=diagnostics,
            )

        messages = self._build_recommendation_messages(outcome)
        cta = self._build_cta() if self._cta_enabled else None

        return await self._finalize(
            sid=sid,
            expected_revision=expected_revision,
            client_message_id=client_message_id,
            client_sequence=client_sequence,
            phase=GuestPhase.COMPLETED,
            messages=messages,
            membership_cta=cta,
            diagnostics=diagnostics,
            extra_guest={
                "last_recommendation": {
                    "category_id": outcome.category_id,
                    "budget_value": outcome.budget_value,
                    "campaign_ids": [c.get("id") for c in outcome.ranked_campaigns],
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            },
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _finalize(
        self,
        *,
        sid: UUID,
        expected_revision: int,
        client_message_id: str,
        client_sequence: int,
        phase: GuestPhase,
        messages: Sequence[dict[str, Any]],
        membership_cta: Optional[dict[str, Any]] = None,
        diagnostics: dict[str, Any],
        extra_guest: Optional[dict[str, Any]] = None,
    ) -> GuestTurnResult:
        guest_value: dict[str, Any] = {"phase": phase.value}
        if extra_guest:
            guest_value.update(extra_guest)

        cas = await self._state.apply_model_update(
            sid,
            expected_revision=expected_revision,
            patch={
                "operation": "SET",
                "path": "/resolved_context/guest",
                "value": guest_value,
            },
            idempotency_key=str(uuid.uuid4()),
            client_message_id=client_message_id,
            client_sequence=client_sequence,
        )

        revision = getattr(cas, "revision", expected_revision + 1)

        return GuestTurnResult(
            session_id=str(sid),
            phase=phase,
            messages=messages,
            state_revision=revision,
            diagnostics=diagnostics,
            membership_cta=membership_cta,
        )

    def _build_recommendation_messages(self, outcome: Any) -> list[dict[str, Any]]:
        budget_txt = f"{outcome.budget_value:,.0f} TL".replace(",", ".")
        cat_name = outcome.category_name or "ihtiyacın"

        intro = (
            f"{budget_txt} bütçene ve {cat_name} ihtiyacına göre "
            f"şu an en uygun {len(outcome.ranked_campaigns)} kampanyayı buldum:"
        )

        messages: list[dict[str, Any]] = [
            {"role": "assistant", "content": intro, "type": "text"}
        ]

        for idx, camp in enumerate(outcome.ranked_campaigns, start=1):
            messages.append(
                {
                    "role": "assistant",
                    "type": "campaign_card",
                    "content": camp.get("summary") or camp.get("title", ""),
                    "card": {
                        "rank": idx,
                        "campaign_id": camp.get("id"),
                        "title": camp.get("title"),
                        "subtitle": camp.get("subtitle"),
                        "bank": camp.get("bank") or camp.get("fin_codes"),
                        "rate": camp.get("rate_text"),
                        "max_amount": camp.get("max_amount"),
                        "max_tenure": camp.get("max_tenure"),
                        "text": camp.get("text"),
                        "score": camp.get("score"),
                    },
                }
            )

        messages.append(
            {
                "role": "assistant",
                "content": (
                    "Bu kampanyalardan yararlanmak için üye olman yeterli. "
                    "Üyelik ücretsiz ve 1 dakikadan az sürer."
                ),
                "type": "text",
            }
        )
        return messages

    def _build_cta(self) -> dict[str, Any]:
        return {
            "label": "Üye ol, kampanyadan yararlan",
            "action": "NAVIGATE_REGISTER",
            "require_membership": True,
            "style": "primary",
        }

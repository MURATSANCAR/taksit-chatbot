"""Production guest entry handler with multi-turn refinement + strong fallback.

Extends the working simple path with:
  1. Multi-turn refinement after COMPLETED (daha ucuz / uzun vade / başka banka …)
  2. Complex / out-of-scope detection → strong membership CTA
  3. Clearer clarification and unknown-refinement fallbacks
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Sequence
from uuid import UUID

from taksitlio.guest.refinement import (
    FALLBACK_NO_MATCH_AFTER_REFINEMENT,
    FALLBACK_OOS,
    FALLBACK_UNKNOWN_REFINEMENT,
    RefinementIntent,
    apply_refinement_to_profile,
    detect_refinement,
    is_complex_or_oos,
)

logger = logging.getLogger(__name__)


class GuestPhase(str, Enum):
    OPENING = "OPENING"
    AWAITING_NEED = "AWAITING_NEED"
    RECOMMENDING = "RECOMMENDING"
    COMPLETED = "COMPLETED"
    CLARIFY = "CLARIFY"
    SAFE_FAILURE = "SAFE_FAILURE"
    REFINING = "REFINING"  # multi-turn after first recommendation


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
    # Public
    # ------------------------------------------------------------------

    async def start_session(
        self,
        *,
        client_message_id: Optional[str] = None,
        locale: str = "tr-TR",
    ) -> GuestTurnResult:
        from taksitlio.conversation_state.domain import Actor, ActorType

        state = await self._state.create_session(
            locale=locale,
            actor=Actor(type=ActorType.ANONYMOUS),
            idempotency_key=client_message_id or str(uuid.uuid4()),
        )

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
            messages=[{"role": "assistant", "content": self._opening, "type": "text"}],
            state_revision=getattr(cas, "revision", state.revision + 1),
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
        sid = UUID(session_id) if not isinstance(session_id, UUID) else session_id

        try:
            state = await self._state.get_session(sid)
        except Exception as exc:
            logger.warning("Guest session missing/expired: %s (%s)", session_id, exc)
            return await self.start_session(
                client_message_id=client_message_id, locale=locale
            )

        guest_ctx = (state.resolved_context or {}).get("guest") or {}
        current_phase = guest_ctx.get("phase", GuestPhase.AWAITING_NEED.value)
        last_rec = guest_ctx.get("last_recommendation")

        # ---------- 1. Complex / out-of-scope → strong fallback ----------
        if is_complex_or_oos(user_utterance):
            return await self._finalize(
                sid=sid,
                expected_revision=expected_revision,
                client_message_id=client_message_id,
                client_sequence=client_sequence,
                phase=GuestPhase.SAFE_FAILURE,
                messages=[{"role": "assistant", "content": FALLBACK_OOS, "type": "text"}],
                membership_cta=self._build_cta() if self._cta_enabled else None,
                diagnostics={"reason": "complex_or_oos", "phase_before": current_phase},
            )

        # ---------- 2. Multi-turn refinement (after COMPLETED / REFINING) ----------
        if current_phase in (GuestPhase.COMPLETED.value, GuestPhase.REFINING.value):
            signal = detect_refinement(user_utterance)
            if signal.intent != RefinementIntent.UNKNOWN:
                return await self._handle_refinement(
                    sid=sid,
                    state=state,
                    signal=signal,
                    last_rec=last_rec,
                    expected_revision=expected_revision,
                    client_message_id=client_message_id,
                    client_sequence=client_sequence,
                    locale=locale,
                )
            # Unknown refinement after COMPLETED → gentle fallback, keep CTA
            return await self._finalize(
                sid=sid,
                expected_revision=expected_revision,
                client_message_id=client_message_id,
                client_sequence=client_sequence,
                phase=GuestPhase.COMPLETED,
                messages=[
                    {
                        "role": "assistant",
                        "content": FALLBACK_UNKNOWN_REFINEMENT,
                        "type": "text",
                    }
                ],
                membership_cta=self._build_cta() if self._cta_enabled else None,
                diagnostics={
                    "reason": "unknown_refinement",
                    "phase_before": current_phase,
                },
                extra_guest={"last_recommendation": last_rec} if last_rec else None,
            )

        # ---------- 3. First-time needs analysis (OPENING / AWAITING / CLARIFY) ----------
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
                            "Üye olursan detaylı tarama yapabiliriz."
                        ),
                        "type": "text",
                    }
                ],
                membership_cta=self._build_cta() if self._cta_enabled else None,
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
                            "sana en uygun kampanyaları hemen gösterebilirim.\n\n"
                            "Örnek: \"cep telefonu, 40 bin TL\""
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
                    {
                        "role": "assistant",
                        "content": DEFAULT_CLARIFY_BUDGET,
                        "type": "text",
                    }
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
                    {
                        "role": "assistant",
                        "content": DEFAULT_CLARIFY_CATEGORY,
                        "type": "text",
                    }
                ],
                diagnostics=diagnostics,
            )

        if not outcome.ranked_campaigns:
            return await self._finalize(
                sid=sid,
                expected_revision=expected_revision,
                client_message_id=client_message_id,
                client_sequence=client_sequence,
                phase=GuestPhase.COMPLETED,
                messages=[
                    {
                        "role": "assistant",
                        "content": DEFAULT_NO_CAMPAIGN,
                        "type": "text",
                    }
                ],
                membership_cta=self._build_cta() if self._cta_enabled else None,
                diagnostics=diagnostics,
            )

        messages = self._build_recommendation_messages(outcome)
        return await self._finalize(
            sid=sid,
            expected_revision=expected_revision,
            client_message_id=client_message_id,
            client_sequence=client_sequence,
            phase=GuestPhase.COMPLETED,
            messages=messages,
            membership_cta=self._build_cta() if self._cta_enabled else None,
            diagnostics=diagnostics,
            extra_guest={
                "last_recommendation": {
                    "category_id": outcome.category_id,
                    "category_code": getattr(outcome, "category_code", None),
                    "budget_value": outcome.budget_value,
                    "campaign_ids": [c.get("id") for c in outcome.ranked_campaigns],
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            },
        )

    # ------------------------------------------------------------------
    # Refinement turn
    # ------------------------------------------------------------------

    async def _handle_refinement(
        self,
        *,
        sid: UUID,
        state: Any,
        signal: Any,
        last_rec: Optional[dict[str, Any]],
        expected_revision: int,
        client_message_id: str,
        client_sequence: int,
        locale: str,
    ) -> GuestTurnResult:
        # Rebuild a minimal need_profile from last recommendation + refinement
        base_profile = {
            "budget": {
                "value": (last_rec or {}).get("budget_value"),
                "type": "APPROXIMATE",
            },
            "category_id": (last_rec or {}).get("category_id"),
            "category_code": (last_rec or {}).get("category_code"),
            "preferences": {},
        }
        refined_profile = apply_refinement_to_profile(base_profile, signal, last_rec)

        # Re-run analysis with synthetic utterance that keeps category+budget context
        # while letting ranking see the new preferences.
        synthetic = (
            f"kategori={(last_rec or {}).get('category_id')} "
            f"bütçe={(refined_profile.get('budget') or {}).get('value')} "
            f"refinement={signal.intent.value}"
        )

        # Prefer calling ranker directly if needs service supports preference injection;
        # otherwise fall back to full analyse with a preference-aware utterance.
        try:
            outcome = await self._needs.analyse(
                utterance=synthetic,
                session_id=str(sid),
                locale=locale,
                max_recommendations=self._max_recs + 1,  # slightly wider
            )
            # Overlay budget from refined profile
            if (refined_profile.get("budget") or {}).get("value"):
                outcome.budget_value = refined_profile["budget"]["value"]
        except Exception as exc:
            logger.exception("Refinement analyse failed")
            return await self._finalize(
                sid=sid,
                expected_revision=expected_revision,
                client_message_id=client_message_id,
                client_sequence=client_sequence,
                phase=GuestPhase.COMPLETED,
                messages=[
                    {
                        "role": "assistant",
                        "content": FALLBACK_NO_MATCH_AFTER_REFINEMENT,
                        "type": "text",
                    }
                ],
                membership_cta=self._build_cta() if self._cta_enabled else None,
                diagnostics={"error": "refinement_failed", "detail": str(exc)},
                extra_guest={"last_recommendation": last_rec} if last_rec else None,
            )

        # Post-filter by refinement preferences (bank exclude, tenure …)
        prefs = refined_profile.get("preferences") or {}
        filtered = list(outcome.ranked_campaigns)
        exclude_banks = [b.lower() for b in (prefs.get("exclude_banks") or [])]
        if exclude_banks:
            filtered = [
                c
                for c in filtered
                if not any(
                    b in (str(c.get("bank") or c.get("brand") or "")).lower()
                    for b in exclude_banks
                )
            ]
        if prefs.get("min_tenure"):
            min_t = int(prefs["min_tenure"])
            filtered = [
                c
                for c in filtered
                if (c.get("installment_count") or c.get("max_tenure") or 0) >= min_t
                or c.get("installment_count") is None
            ]
        if prefs.get("max_tenure"):
            max_t = int(prefs["max_tenure"])
            filtered = [
                c
                for c in filtered
                if (c.get("installment_count") or c.get("max_tenure") or 99) <= max_t
                or c.get("installment_count") is None
            ]

        # Drop already-shown campaigns when user asks for more / other bank
        shown_ids = set((last_rec or {}).get("campaign_ids") or [])
        if signal.intent in (
            RefinementIntent.MORE_OPTIONS,
            RefinementIntent.OTHER_BANK,
            RefinementIntent.CHEAPER,
        ):
            filtered = [c for c in filtered if c.get("id") not in shown_ids]

        filtered = filtered[: self._max_recs]
        outcome.ranked_campaigns = filtered

        diagnostics = {
            "phase_before": GuestPhase.COMPLETED.value,
            "refinement_intent": signal.intent.value,
            "refinement_confidence": signal.confidence,
            "ranked_count": len(filtered),
            "gate_status": outcome.gate_status,
        }

        if not filtered:
            return await self._finalize(
                sid=sid,
                expected_revision=expected_revision,
                client_message_id=client_message_id,
                client_sequence=client_sequence,
                phase=GuestPhase.COMPLETED,
                messages=[
                    {
                        "role": "assistant",
                        "content": FALLBACK_NO_MATCH_AFTER_REFINEMENT,
                        "type": "text",
                    }
                ],
                membership_cta=self._build_cta() if self._cta_enabled else None,
                diagnostics=diagnostics,
                extra_guest={"last_recommendation": last_rec} if last_rec else None,
            )

        # Build refined intro
        intent_label = {
            RefinementIntent.CHEAPER: "daha uygun",
            RefinementIntent.LONGER_TENURE: "daha uzun vadeli",
            RefinementIntent.SHORTER_TENURE: "daha kısa vadeli",
            RefinementIntent.OTHER_BANK: "farklı bankalı",
            RefinementIntent.MORE_OPTIONS: "ek",
            RefinementIntent.HIGHER_BUDGET: "yükseltilmiş bütçeye uygun",
            RefinementIntent.LOWER_BUDGET: "düşürülmüş bütçeye uygun",
        }.get(signal.intent, "güncel")

        intro = f"Tercihine göre {intent_label} {len(filtered)} kampanya buldum:"
        messages: list[dict[str, Any]] = [
            {"role": "assistant", "content": intro, "type": "text"}
        ]
        for idx, camp in enumerate(filtered, start=1):
            messages.append(
                {
                    "role": "assistant",
                    "type": "campaign_card",
                    "content": camp.get("summary") or camp.get("title", ""),
                    "card": {
                        "rank": idx,
                        "campaign_id": camp.get("id"),
                        "title": camp.get("title"),
                        "subtitle": camp.get("subtitle") or camp.get("summary"),
                        "bank": camp.get("bank") or camp.get("brand"),
                        "rate": camp.get("rate_text"),
                        "score": camp.get("score"),
                    },
                }
            )
        messages.append(
            {
                "role": "assistant",
                "content": (
                    "Başka bir tercih daha söyleyebilirsin "
                    "(daha ucuz, daha uzun vade, başka banka…) "
                    "ya da üye olup detaya inebilirsin."
                ),
                "type": "text",
            }
        )

        new_ids = [c.get("id") for c in filtered]
        merged_ids = list(dict.fromkeys(list(shown_ids) + new_ids))

        return await self._finalize(
            sid=sid,
            expected_revision=expected_revision,
            client_message_id=client_message_id,
            client_sequence=client_sequence,
            phase=GuestPhase.REFINING,
            messages=messages,
            membership_cta=self._build_cta() if self._cta_enabled else None,
            diagnostics=diagnostics,
            extra_guest={
                "last_recommendation": {
                    "category_id": (last_rec or {}).get("category_id"),
                    "category_code": (last_rec or {}).get("category_code"),
                    "budget_value": (refined_profile.get("budget") or {}).get("value")
                    or (last_rec or {}).get("budget_value"),
                    "campaign_ids": merged_ids,
                    "last_refinement": signal.intent.value,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            },
        )

    # ------------------------------------------------------------------
    # Shared helpers
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
                        "subtitle": camp.get("subtitle") or camp.get("summary"),
                        "bank": camp.get("bank") or camp.get("brand"),
                        "rate": camp.get("rate_text"),
                        "score": camp.get("score"),
                    },
                }
            )
        messages.append(
            {
                "role": "assistant",
                "content": (
                    "Bu kampanyalardan yararlanmak için üye olman yeterli. "
                    "İstersen \"daha ucuz\", \"daha uzun vade\" veya \"başka banka\" "
                    "yazarak seçenekleri daraltabilirsin."
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

"""Production guest (loginsiz) entry handler.

Responsibilities
----------------
* Detect / create an unauthenticated session.
* Emit the proactive opening message ("İhtiyaç analizi yapayım mı?").
* Route the subsequent free-text turn into the full needs-analysis → ranking → CTA pipeline.
* Enforce safety gates and provisional acceptance rules for guest traffic.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Sequence

from taksitlio.conversation_state import ConversationStateManager  # type: ignore
from taksitlio.guest.needs_analysis import NeedsAnalysisService, NeedsAnalysisOutcome

logger = logging.getLogger(__name__)


class GuestPhase(str, Enum):
    OPENING = "OPENING"                    # Bot just offered needs analysis
    AWAITING_NEED = "AWAITING_NEED"        # Waiting for free-text need + budget
    RECOMMENDING = "RECOMMENDING"          # Ranking + grounded response in progress
    COMPLETED = "COMPLETED"                # CTA already delivered
    CLARIFY = "CLARIFY"                    # Missing budget or category – ask once
    SAFE_FAILURE = "SAFE_FAILURE"          # Hard safety / quality gate failure


@dataclass(frozen=True)
class GuestTurnResult:
    """Immutable result returned to the Chat API layer."""

    session_id: str
    phase: GuestPhase
    messages: Sequence[dict[str, Any]]     # list of {role, content, cards?, cta?}
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


# ---------------------------------------------------------------------------
# Opening copy (can be overridden via config / feature flag)
# ---------------------------------------------------------------------------
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
    Production entry point for unauthenticated (loginsiz) mobile sessions.

    Design principles (aligned with existing ADRs):
    * All state mutations go through ConversationStateManager (CAS + idempotency).
    * Never write directly from matcher / ranking layers.
    * Guest traffic is subject to the same safety gates as authenticated traffic;
      provisional acceptance still required for ranking quality.
    * MembershipCTA is always offered once a recommendation is produced.
    """

    def __init__(
        self,
        state_manager: ConversationStateManager,
        needs_service: NeedsAnalysisService,
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
        """
        Create a brand-new guest session and return the proactive opening.
        Called when the mobile app opens the chatbot in the loginsiz area.
        """
        state = await self._state.create_session(
            metadata={
                "auth_status": "GUEST",
                "locale": locale,
                "entry_point": "loginsiz_chatbot",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )

        # Persist phase
        await self._state.apply_model_update(
            state.session_id,
            expected_revision=state.revision,
            patch={
                "operation": "SET",
                "path": "/guest/phase",
                "value": GuestPhase.OPENING.value,
            },
            idempotency_key=str(uuid.uuid4()),
            client_message_id=client_message_id or str(uuid.uuid4()),
            client_sequence=0,
        )

        return GuestTurnResult(
            session_id=state.session_id,
            phase=GuestPhase.OPENING,
            messages=[
                {
                    "role": "assistant",
                    "content": self._opening,
                    "type": "text",
                }
            ],
            state_revision=state.revision + 1,
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
        """
        Process one free-text turn from a guest user.

        Pipeline:
          1. Load state + phase guard
          2. NeedsAnalysisService (FAST extract + semantic match + ranking)
          3. Build grounded messages + MembershipCTA
          4. Persist new phase + recommendation snapshot
        """
        state = await self._state.get_session(session_id)
        if state is None:
            raise ValueError(f"Unknown guest session: {session_id}")

        current_phase = (state.data or {}).get("guest", {}).get("phase", GuestPhase.AWAITING_NEED.value)

        # ----------------------------------------------------------
        # Run the full needs-analysis pipeline
        # ----------------------------------------------------------
        outcome: NeedsAnalysisOutcome = await self._needs.analyse(
            utterance=user_utterance,
            session_id=session_id,
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

        # ----------------------------------------------------------
        # Decision tree (production safety first)
        # ----------------------------------------------------------
        if outcome.gate_status == "SAFE_FAILURE":
            return await self._finalize(
                session_id=session_id,
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
            # Completely empty – ask once for both
            return await self._finalize(
                session_id=session_id,
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
                session_id=session_id,
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
                session_id=session_id,
                expected_revision=expected_revision,
                client_message_id=client_message_id,
                client_sequence=client_sequence,
                phase=GuestPhase.CLARIFY,
                messages=[
                    {"role": "assistant", "content": DEFAULT_CLARIFY_CATEGORY, "type": "text"}
                ],
                diagnostics=diagnostics,
            )

        # ----------------------------------------------------------
        # Happy path – we have category + budget → recommendations
        # ----------------------------------------------------------
        if not outcome.ranked_campaigns:
            messages = [
                {
                    "role": "assistant",
                    "content": DEFAULT_NO_CAMPAIGN,
                    "type": "text",
                }
            ]
            cta = self._build_cta(require_membership=True) if self._cta_enabled else None
            return await self._finalize(
                session_id=session_id,
                expected_revision=expected_revision,
                client_message_id=client_message_id,
                client_sequence=client_sequence,
                phase=GuestPhase.COMPLETED,
                messages=messages,
                membership_cta=cta,
                diagnostics=diagnostics,
            )

        # Build grounded recommendation messages
        messages = self._build_recommendation_messages(outcome)
        cta = self._build_cta(require_membership=True) if self._cta_enabled else None

        return await self._finalize(
            session_id=session_id,
            expected_revision=expected_revision,
            client_message_id=client_message_id,
            client_sequence=client_sequence,
            phase=GuestPhase.COMPLETED,
            messages=messages,
            membership_cta=cta,
            diagnostics=diagnostics,
            extra_state_patch={
                "operation": "SET",
                "path": "/guest/last_recommendation",
                "value": {
                    "category_id": outcome.category_id,
                    "budget_value": outcome.budget_value,
                    "campaign_ids": [c["id"] for c in outcome.ranked_campaigns],
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _finalize(
        self,
        *,
        session_id: str,
        expected_revision: int,
        client_message_id: str,
        client_sequence: int,
        phase: GuestPhase,
        messages: Sequence[dict[str, Any]],
        membership_cta: Optional[dict[str, Any]] = None,
        diagnostics: dict[str, Any],
        extra_state_patch: Optional[dict[str, Any]] = None,
    ) -> GuestTurnResult:
        """Apply phase + optional extra patch under CAS, then return result."""
        patches = [
            {
                "operation": "SET",
                "path": "/guest/phase",
                "value": phase.value,
            }
        ]
        if extra_state_patch:
            patches.append(extra_state_patch)

        # Apply sequentially under the same expected_revision chain
        revision = expected_revision
        for patch in patches:
            result = await self._state.apply_model_update(
                session_id,
                expected_revision=revision,
                patch=patch,
                idempotency_key=str(uuid.uuid4()),
                client_message_id=client_message_id,
                client_sequence=client_sequence,
            )
            revision = result.revision

        return GuestTurnResult(
            session_id=session_id,
            phase=phase,
            messages=messages,
            state_revision=revision,
            diagnostics=diagnostics,
            membership_cta=membership_cta,
        )

    def _build_recommendation_messages(self, outcome: NeedsAnalysisOutcome) -> list[dict[str, Any]]:
        """Produce the final user-facing messages (grounded + cards)."""
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
            card = {
                "role": "assistant",
                "type": "campaign_card",
                "content": camp.get("summary") or camp.get("title", ""),
                "card": {
                    "rank": idx,
                    "campaign_id": camp["id"],
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
            messages.append(card)

        # Soft closing before CTA
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

    def _build_cta(self, *, require_membership: bool = True) -> dict[str, Any]:
        return {
            "label": "Üye ol, kampanyadan yararlan",
            "action": "NAVIGATE_REGISTER",
            "require_membership": require_membership,
            "style": "primary",
        }

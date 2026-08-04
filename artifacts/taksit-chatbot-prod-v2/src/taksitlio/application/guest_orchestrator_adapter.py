"""
Production GuestOrchestratorAdapter – real container wiring.

Mevcut AppContainer yapısına (container.py) göre yazılmıştır:

  container.pipeline
  container.extras["sessions"]          → ConversationStateManager
  container.extras["campaign_repo"]
  container.extras.get("category_matcher") / pipeline içindeki matcher
  container.extras.get("search_orchestrator")

FAST extractor ve ranking, mevcut pipeline / CampaignRetriever üzerinden
veya explicit component olarak inject edilir.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)


class GuestOrchestratorAdapter:
    """
    Chat API GUEST branch'inin tek giriş noktası.
    """

    def __init__(self, handler: Any) -> None:
        self._handler = handler

    # ------------------------------------------------------------------
    # Factory – gerçek container'dan kurulum
    # ------------------------------------------------------------------

    @classmethod
    def from_container(cls, container: Any) -> "GuestOrchestratorAdapter":
        """
        AppContainer → GuestEntryHandler zincirini kurar.

        Container attribute isimleri mevcut `app/container.py` ile uyumludur.
        Eksik component varsa graceful degradation (in-memory / no-op) yapılır
        ki production'da crash olmasın.
        """
        from taksitlio.guest.entry import GuestEntryHandler
        from taksitlio.guest.needs_analysis import NeedsAnalysisService

        extras = getattr(container, "extras", {}) or {}

        # 1. Conversation state (zorunlu)
        state_manager = extras.get("sessions")
        if state_manager is None:
            raise RuntimeError(
                "Guest flow requires container.extras['sessions'] "
                "(ConversationStateManager). Check build_*_container()."
            )

        # 2. FAST extractor – pipeline veya explicit
        fast_extractor = (
            extras.get("fast_extractor")
            or getattr(getattr(container, "pipeline", None), "fast_extractor", None)
            or _NullFastExtractor()
        )

        # 3. Semantic matcher
        semantic_matcher = (
            extras.get("category_matcher")
            or getattr(getattr(container, "pipeline", None), "category_matcher", None)
            or _NullMatcher()
        )

        # 4. Campaign repo + ranking + eligibility
        campaign_repo = extras.get("campaign_repo") or _NullCampaignRepo()
        ranker = extras.get("campaign_ranker") or _build_default_ranker()
        eligibility = extras.get("eligibility_engine") or _build_default_eligibility()

        # 5. Category catalog (opsiyonel)
        category_catalog = extras.get("category_repo") or extras.get("category_catalog")

        needs = NeedsAnalysisService(
            fast_extractor=fast_extractor,
            semantic_matcher=semantic_matcher,
            campaign_ranker=ranker,
            eligibility_engine=eligibility,
            campaign_repository=campaign_repo,
            category_catalog=category_catalog,
            quality_gate_policy=extras.get("quality_gate_policy"),
        )

        handler = GuestEntryHandler(
            state_manager=state_manager,
            needs_service=needs,
            max_recommendations=2,
            membership_cta_enabled=True,
        )
        return cls(handler)

    # ------------------------------------------------------------------
    # Public API (Chat route tarafından çağrılır)
    # ------------------------------------------------------------------

    async def start_guest_session(
        self,
        *,
        client_message_id: Optional[str] = None,
        locale: str = "tr-TR",
    ) -> dict[str, Any]:
        result = await self._handler.start_session(
            client_message_id=client_message_id,
            locale=locale,
        )
        return result.to_api_payload()

    async def handle_guest_turn(
        self,
        *,
        session_id: str,
        utterance: str,
        expected_revision: int = 0,
        client_message_id: Optional[str] = None,
        client_sequence: int = 1,
        locale: str = "tr-TR",
    ) -> dict[str, Any]:
        result = await self._handler.handle_turn(
            session_id=session_id,
            user_utterance=utterance,
            expected_revision=expected_revision,
            client_message_id=client_message_id or str(uuid.uuid4()),
            client_sequence=client_sequence,
            locale=locale,
        )
        return result.to_api_payload()


# ---------------------------------------------------------------------------
# Graceful null objects (component henüz container'a eklenmemişse crash önler)
# ---------------------------------------------------------------------------

class _NullFastExtractor:
    async def extract(self, utterance: str, locale: str = "tr-TR") -> dict:
        # Minimal fallback – gerçek FAST yoksa en azından boş döner
        return {
            "intent": {"type": "UNKNOWN"},
            "budget": {},
            "category_signals": {"positive": [], "negative": []},
        }


class _NullMatcher:
    async def match(self, query: dict) -> dict:
        return {"status": "NO_MATCH"}


class _NullCampaignRepo:
    async def list_active(self, category_id=None, locale="tr-TR"):
        return []


def _build_default_ranker():
    """Mevcut RankingEngine varsa onu kullan, yoksa basit score sıralayıcı."""
    try:
        from taksitlio.campaign.ranking import RankingEngine  # type: ignore
        return RankingEngine()
    except Exception:
        class _SimpleRanker:
            def rank(self, campaigns, need_profile, max_results=2, weights=None):
                # En basit: ilk N kampanyayı skor 0.5 ile döndür
                out = []
                for i, c in enumerate(campaigns[:max_results]):
                    c = dict(c)
                    c["score"] = 0.5 - (i * 0.05)
                    out.append(c)
                return out
        return _SimpleRanker()


def _build_default_eligibility():
    try:
        from taksitlio.campaign.eligibility import EligibilityEngine  # type: ignore
        return EligibilityEngine()
    except Exception:
        class _AlwaysEligible:
            def is_eligible(self, campaign, need_profile):
                return True
        return _AlwaysEligible()

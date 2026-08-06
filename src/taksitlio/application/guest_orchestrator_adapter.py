"""
Production GuestOrchestratorAdapter — CAMPAIGN ONLY.

Replaces UniversalGuest / product-capable guest path.
Guest never touches ChatPipeline or search_sessions.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)


class GuestOrchestratorAdapter:
    """Chat API GUEST branch tek giriş — CampaignOnlyGuestPipeline."""

    def __init__(self, pipeline: Any) -> None:
        self._pipeline = pipeline

    @classmethod
    def from_container(cls, container: Any) -> "GuestOrchestratorAdapter":
        from taksitlio.guest.campaign_only_pipeline import CampaignOnlyGuestPipeline

        extras = getattr(container, "extras", {}) or {}

        state_manager = (
            extras.get("conversation_state")
            or extras.get("conversation_state_manager")
        )
        if state_manager is None:
            candidate = extras.get("sessions")
            if candidate is not None and hasattr(candidate, "create_session"):
                state_manager = candidate
        if state_manager is None:
            raise RuntimeError(
                "Guest CAMPAIGN_ONLY requires extras['conversation_state'] "
                "or extras['sessions'] with create_session()"
            )

        campaign_repo = extras.get("campaign_repo")
        if campaign_repo is None:
            pipe = getattr(container, "pipeline", None)
            campaign_repo = getattr(pipe, "_campaign_repo", None) if pipe else None
        if campaign_repo is None:
            # Last resort: in-memory empty (still no products)
            campaign_repo = _EmptyCampaignRepo()
            logger.warning("campaign_repo missing — guest will return NO_CAMPAIGN")

        ranker = extras.get("campaign_ranker")
        eligibility = extras.get("eligibility_engine")

        # Tier-2 small-LLM category fallback. Prefer an injected instance;
        # otherwise build from env (GUEST_FAST_* / FAST_*). Absent config →
        # None → pure deterministic guest (no network, no behaviour change).
        category_llm = extras.get("guest_category_llm")
        if category_llm is None:
            try:
                from taksitlio.guest.llm_fallback import GuestCategoryResolverLLM

                category_llm = GuestCategoryResolverLLM.from_env()
            except Exception:  # noqa: BLE001 — never fail guest startup on this
                category_llm = None

        pipeline = CampaignOnlyGuestPipeline(
            state_manager=state_manager,
            campaign_repo=campaign_repo,
            ranker=ranker,
            eligibility=eligibility,
            max_campaigns=2,
            category_llm=category_llm,
        )
        return cls(pipeline)

    async def start_guest_session(
        self, *, locale: str = "tr-TR", session_id: Optional[str] = None
    ) -> dict[str, Any]:
        sid = None
        if session_id and session_id not in ("new", "null", ""):
            try:
                sid = uuid.UUID(str(session_id))
            except Exception:
                sid = None
        return await self._pipeline.start(locale=locale, session_id=sid)

    async def handle_guest_turn(
        self,
        *,
        session_id: str,
        utterance: str,
        expected_revision: int = 0,
        client_message_id: Optional[str] = None,
        client_sequence: Optional[int] = None,
        locale: str = "tr-TR",
    ) -> dict[str, Any]:
        return await self._pipeline.handle(
            session_id=session_id,
            utterance=utterance,
            expected_revision=expected_revision,
            client_message_id=client_message_id or str(uuid.uuid4()),
            client_sequence=client_sequence,
            locale=locale,
        )


class _EmptyCampaignRepo:
    async def list_by_category_codes(self, category_codes, *, limit: int = 50):
        return []

    async def list_active(self, category_id=None, locale="tr-TR"):
        return []

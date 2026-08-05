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

        pipeline = CampaignOnlyGuestPipeline(
            state_manager=state_manager,
            campaign_repo=campaign_repo,
            ranker=ranker,
            eligibility=eligibility,
            max_campaigns=2,
        )
        return cls(pipeline)

    async def start_guest_session(self, *, locale: str = "tr-TR") -> dict[str, Any]:
        return await self._pipeline.start(locale=locale)

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

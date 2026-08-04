"""
Thin production adapter that wires the new GuestEntryHandler into the
existing ChatOrchestrator / Chat API surface.

This file is intentionally small. It does **not** replace the current
orchestrator; it only adds the loginsiz entry path and the
needs-analysis → ranking → CTA chain for guest sessions.

Integration points (existing code):
  * ConversationStateManager   – already used
  * DeterministicFastExtractor / FastNeedUnderstanding
  * SemanticCategoryMatcher
  * campaign.ranking.RankingEngine
  * campaign.eligibility.EligibilityEngine
  * campaign.repository.CampaignRepository
  * response.grounded (MembershipCTA already exists)
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from taksitlio.guest.entry import GuestEntryHandler, GuestTurnResult
from taksitlio.guest.needs_analysis import NeedsAnalysisService

logger = logging.getLogger(__name__)


class GuestOrchestratorAdapter:
    """
    Drop-in adapter for the Chat API layer.

    Usage inside the existing FastAPI / HTTP handler:

        adapter = GuestOrchestratorAdapter.from_container(container)
        if session.is_guest:
            result = await adapter.handle(...)
            return result.to_api_payload()
        else:
            # existing authenticated path
            ...
    """

    def __init__(self, handler: GuestEntryHandler) -> None:
        self._handler = handler

    @classmethod
    def from_container(cls, container: Any) -> "GuestOrchestratorAdapter":
        """
        Build from the project's existing dependency-injection container.
        Adjust the attribute names to match the real container once integrated.
        """
        needs = NeedsAnalysisService(
            fast_extractor=container.fast_extractor,
            semantic_matcher=container.semantic_matcher,
            campaign_ranker=container.campaign_ranker,
            eligibility_engine=container.eligibility_engine,
            campaign_repository=container.campaign_repository,
            category_catalog=container.category_catalog,
            quality_gate_policy=getattr(container, "quality_gate_policy", None),
        )
        handler = GuestEntryHandler(
            state_manager=container.conversation_state_manager,
            needs_service=needs,
            max_recommendations=2,
            membership_cta_enabled=True,
        )
        return cls(handler)

    async def start_guest_session(
        self,
        *,
        client_message_id: Optional[str] = None,
        locale: str = "tr-TR",
    ) -> dict[str, Any]:
        result: GuestTurnResult = await self._handler.start_session(
            client_message_id=client_message_id,
            locale=locale,
        )
        return result.to_api_payload()

    async def handle_guest_turn(
        self,
        *,
        session_id: str,
        utterance: str,
        expected_revision: int,
        client_message_id: str,
        client_sequence: int,
        locale: str = "tr-TR",
    ) -> dict[str, Any]:
        result: GuestTurnResult = await self._handler.handle_turn(
            session_id=session_id,
            user_utterance=utterance,
            expected_revision=expected_revision,
            client_message_id=client_message_id,
            client_sequence=client_sequence,
            locale=locale,
        )
        return result.to_api_payload()

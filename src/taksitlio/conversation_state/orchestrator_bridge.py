"""Orchestrator integration contract — router never writes state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from uuid import UUID

from taksitlio.conversation_state.domain import CompareAndSetResult, ConversationState
from taksitlio.conversation_state.errors import ConversationVersionConflict
from taksitlio.conversation_state.manager import ConversationStateManager


@dataclass(frozen=True)
class OrchestrationConflict:
    """Typed conflict for chat orchestrator — do not blind-retry model output."""

    session_id: UUID
    expected_revision: int
    actual_revision: int | None
    require_reevaluation: bool = True


class ChatOrchestratorPort(Protocol):
    """
    Integration surface for the next chat orchestration layer.

    Flow:
      get_session → snapshot.revision
      ModelRouter.route(...)  # no state writes
      apply_model_update(expected_revision=snapshot.revision)
      on VERSION_CONFLICT → re-read once, re-evaluate; second conflict → return conflict
    """

    async def apply_router_result(
        self,
        session_id: UUID,
        *,
        snapshot: ConversationState,
        patch_or_need: Mapping[str, Any],
        idempotency_key: str,
        client_message_id: str,
        client_sequence: int | None = None,
        correlation_id: str | None = None,
        max_retries: int = 1,
    ) -> CompareAndSetResult | OrchestrationConflict: ...


class DefaultOrchestratorBridge:
    def __init__(self, manager: ConversationStateManager) -> None:
        self._manager = manager

    async def apply_router_result(
        self,
        session_id: UUID,
        *,
        snapshot: ConversationState,
        patch_or_need: Mapping[str, Any],
        idempotency_key: str,
        client_message_id: str,
        client_sequence: int | None = None,
        correlation_id: str | None = None,
        max_retries: int = 1,
    ) -> CompareAndSetResult | OrchestrationConflict:
        attempts = 0
        current = snapshot
        while True:
            try:
                if patch_or_need.get("operation"):
                    return await self._manager.apply_model_update(
                        session_id,
                        expected_revision=current.revision,
                        patch=patch_or_need,
                        idempotency_key=idempotency_key,
                        client_message_id=client_message_id,
                        client_sequence=client_sequence,
                        correlation_id=correlation_id,
                    )
                return await self._manager.initialize_need(
                    session_id,
                    expected_revision=current.revision,
                    need=patch_or_need,
                    idempotency_key=idempotency_key,
                    client_message_id=client_message_id,
                    client_sequence=client_sequence,
                    correlation_id=correlation_id,
                )
            except ConversationVersionConflict as exc:
                attempts += 1
                if attempts > max_retries:
                    return OrchestrationConflict(
                        session_id=session_id,
                        expected_revision=current.revision,
                        actual_revision=exc.actual_revision,
                        require_reevaluation=True,
                    )
                # Re-read; do NOT reuse stale model output blindly — caller must re-evaluate.
                current = await self._manager.get_session(session_id)
                return OrchestrationConflict(
                    session_id=session_id,
                    expected_revision=snapshot.revision,
                    actual_revision=current.revision,
                    require_reevaluation=True,
                )

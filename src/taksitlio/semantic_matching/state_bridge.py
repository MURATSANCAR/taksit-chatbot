"""Bridge that maps CategoryMatchResult into ConversationState via the manager.

The matcher is a pure read-side component. State mutation is the
ConversationStateManager's job (ADR-003). This bridge translates a match
result into a bounded set of patch operations that touch only the
`category_resolution` region of the active_need — never embeddings,
projections or alias lists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from taksitlio.conversation_state.domain import CasStatus, CompareAndSetResult
from taksitlio.conversation_state.manager import ConversationStateManager
from taksitlio.semantic_matching.domain import CategoryMatchResult


@dataclass(frozen=True)
class CategoryResolutionApplyOutcome:
    result: Optional[CompareAndSetResult]
    applied: bool
    reason: Optional[str] = None


def _candidate_dict(candidate) -> dict:
    return {
        "category_id": candidate.category_id,
        "slug": candidate.slug,
        "display_name": candidate.display_name,
        "score": float(candidate.score),
        "rank": int(candidate.rank),
    }


class CategoryResolutionApplier:
    """Applies a CategoryMatchResult to conversation state via the manager."""

    def __init__(self, manager: ConversationStateManager) -> None:
        self._manager = manager

    async def apply(
        self,
        *,
        session_id: UUID,
        expected_revision: int,
        match_result: CategoryMatchResult,
        idempotency_key: str,
        client_message_id: str,
        client_sequence: Optional[int] = None,
        correlation_id: Optional[str] = None,
    ) -> CategoryResolutionApplyOutcome:
        payload = {
            "selected_category_id": match_result.selected_category_id,
            "candidates": [_candidate_dict(c) for c in match_result.candidates],
            "catalog_id": match_result.catalog_id,
            "catalog_revision": match_result.catalog_revision,
            "match_status": match_result.decision.status.value,
        }
        patch = {
            "operation": "SET",
            "path": "/active_need/category_resolution",
            "value": payload,
            "confidence": _confidence_from_status(match_result),
        }
        result = await self._manager.apply_model_update(
            session_id,
            expected_revision=expected_revision,
            patch=patch,
            idempotency_key=idempotency_key,
            client_message_id=client_message_id,
            client_sequence=client_sequence,
            correlation_id=correlation_id,
        )
        return CategoryResolutionApplyOutcome(
            result=result,
            applied=result.status == CasStatus.APPLIED,
            reason=result.detail,
        )


def _confidence_from_status(result: CategoryMatchResult) -> float:
    if not result.candidates:
        return 0.4
    top = result.candidates[0]
    return max(0.0, min(1.0, float(top.score)))


__all__ = [
    "CategoryResolutionApplier",
    "CategoryResolutionApplyOutcome",
]

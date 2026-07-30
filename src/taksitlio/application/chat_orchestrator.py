"""ChatOrchestrator — ADR-007 §D.

Owns the "one user turn" flow:

    1. Fetch conversation snapshot (via ConversationStateManager).
    2. FAST need understanding on the raw utterance.
    3. SemanticConstraintValidator on the FAST output.
    4. First CAS: apply validated need-profile projection through the
       manager (typed patch on ``/active_need``).
    5. SemanticCategoryMatcher.match(MatchQuery).
    6. Second CAS: CategoryResolutionApplier writes category_resolution.

Guarantees:

* Router and matcher never mutate state — only the manager does.
* On a version conflict during step 4 or step 6, the orchestrator
  re-fetches the snapshot at most ONCE and retries. A second conflict
  raises :class:`OrchestratorRuntimeError` with reason
  ``VERSION_CONFLICT_UNRECOVERABLE``.
* When FAST fails (deployment unavailable), the orchestrator records
  the failure and does NOT invoke the matcher on that turn.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional
from uuid import UUID, uuid4

from taksitlio.conversation_state.domain import CasStatus, ConversationState
from taksitlio.conversation_state.errors import ConversationVersionConflict
from taksitlio.conversation_state.manager import ConversationStateManager
from taksitlio.semantic_constraints import (
    SemanticConstraintValidator,
    ValidatedSemanticConstraints,
)
from taksitlio.semantic_matching import (
    CategoryMatchResult,
    CategoryResolutionApplier,
    MatchQuery,
    SemanticCategoryMatcher,
)
from taksitlio.understanding.fast import (
    FastDeploymentUnavailable,
    FastExtractionError,
    FastExtractionOutcome,
    FastNeedUnderstanding,
)


class OrchestratorRuntimeError(Exception):
    """Raised when the orchestrator cannot finish the turn safely."""

    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class OrchestratorInputs:
    session_id: UUID
    utterance: str
    client_message_id: str
    idempotency_key: str
    client_sequence: Optional[int] = None
    correlation_id: Optional[str] = None
    locale: str = "tr-TR"


@dataclass(frozen=True)
class ChatTurnOutcome:
    """Result of one orchestrated turn."""

    session_id: UUID
    fast: Optional[FastExtractionOutcome]
    validated_constraints: Optional[ValidatedSemanticConstraints]
    match_result: Optional[CategoryMatchResult]
    revision_after: Optional[int]
    fast_failure_reason: Optional[str] = None
    matcher_skipped: bool = False
    reevaluation_count: int = 0
    duration_ms: float = 0.0
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


class ChatOrchestrator:
    def __init__(
        self,
        *,
        conversation_state: ConversationStateManager,
        fast_understanding: FastNeedUnderstanding,
        matcher: SemanticCategoryMatcher,
        constraint_validator: Optional[SemanticConstraintValidator] = None,
        resolution_applier: Optional[CategoryResolutionApplier] = None,
        catalog_id: str,
        embedding_profile_id: str,
        catalog_revision: int,
    ) -> None:
        self._state = conversation_state
        self._fast = fast_understanding
        self._matcher = matcher
        self._validator = constraint_validator or SemanticConstraintValidator()
        self._applier = resolution_applier or CategoryResolutionApplier(
            conversation_state
        )
        self._catalog_id = catalog_id
        self._embedding_profile_id = embedding_profile_id
        self._catalog_revision = catalog_revision

    async def handle_turn(self, inputs: OrchestratorInputs) -> ChatTurnOutcome:
        started = time.perf_counter()

        # ---- Step 1: snapshot
        snapshot = await self._state.get_session(inputs.session_id)

        # ---- Step 2 + 3: FAST + validate
        fast_outcome: Optional[FastExtractionOutcome] = None
        validated: Optional[ValidatedSemanticConstraints] = None
        fast_reason: Optional[str] = None
        try:
            fast_outcome = await self._fast.extract(
                inputs.utterance, locale=inputs.locale
            )
        except FastDeploymentUnavailable as exc:
            fast_reason = exc.reason_code or "FAST_DEPLOYMENT_UNAVAILABLE"
        except FastExtractionError as exc:
            fast_reason = exc.reason_code or "FAST_EXTRACTION_ERROR"

        if fast_outcome is not None:
            validated = self._validator.validate(
                fast_outcome.constraints.to_matcher_dict()
            )

        # ---- Step 4: first CAS — write need_profile hash / summary
        # No FAST → we cannot safely mutate state, and skipping the
        # matcher is the correct thing. Callers observe ``fast_failure_reason``.
        revision_after: Optional[int] = snapshot.revision
        reevaluations = 0
        if fast_outcome is not None:
            snapshot, revision_after, reevaluations = await self._commit_need_profile(
                snapshot,
                inputs=inputs,
                fast_outcome=fast_outcome,
                validated=validated,
                reevaluations=reevaluations,
            )

        # ---- Step 5: matcher
        match_result: Optional[CategoryMatchResult] = None
        if fast_outcome is not None and validated is not None:
            query = MatchQuery(
                need_description=inputs.utterance,
                catalog_id=self._catalog_id,
                locale=inputs.locale,
                embedding_profile_id=self._embedding_profile_id,
                catalog_revision=self._catalog_revision,
                session_id=str(inputs.session_id),
                semantic_constraints=validated.to_matcher_dict(),
            )
            match_result = await self._matcher.match(query)

        # ---- Step 6: second CAS — write category_resolution
        if match_result is not None:
            snapshot, revision_after, reevaluations = (
                await self._commit_category_resolution(
                    snapshot,
                    inputs=inputs,
                    match_result=match_result,
                    reevaluations=reevaluations,
                )
            )

        duration_ms = (time.perf_counter() - started) * 1000.0
        return ChatTurnOutcome(
            session_id=inputs.session_id,
            fast=fast_outcome,
            validated_constraints=validated,
            match_result=match_result,
            revision_after=revision_after,
            fast_failure_reason=fast_reason,
            matcher_skipped=match_result is None,
            reevaluation_count=reevaluations,
            duration_ms=duration_ms,
            diagnostics={
                "input_utterance_len": len(inputs.utterance or ""),
                "rejected_constraint_reasons": list(
                    validated.rejected_reasons if validated else ()
                ),
            },
        )

    # ------------------------------------------------------------------
    # Internal CAS helpers
    # ------------------------------------------------------------------

    async def _commit_need_profile(
        self,
        snapshot: ConversationState,
        *,
        inputs: OrchestratorInputs,
        fast_outcome: FastExtractionOutcome,
        validated: Optional[ValidatedSemanticConstraints],
        reevaluations: int,
    ) -> tuple[ConversationState, int, int]:
        """First CAS write — bounded, typed patch on active_need summary."""

        summary_patch = {
            "operation": "SET",
            "path": "/active_need/understanding_summary",
            "value": {
                "extractor": fast_outcome.extractor,
                "intent": fast_outcome.need_profile.get("intent", {}).get("type"),
                "positive_concepts": [
                    c.concept for c in (validated.positive if validated else ())
                ][:8],
                "negative_concepts": [
                    c.concept for c in (validated.negative if validated else ())
                ][:8],
                "correction_pairs": [
                    {
                        "previous_concept": c.previous_concept,
                        "replacement_concept": c.replacement_concept,
                    }
                    for c in (validated.corrections if validated else ())
                ][:4],
                "multi_need": bool(
                    fast_outcome.diagnostics.get("multi_need", False)
                ),
            },
            "confidence": float(
                fast_outcome.need_profile.get("confidence", 0.7)
            ),
        }
        # Suffix idempotency keys so the two CAS writes don't collide.
        idem = f"{inputs.idempotency_key}:need"
        client_msg = f"{inputs.client_message_id}:need"
        try:
            result = await self._state.apply_model_update(
                snapshot.session_id,
                expected_revision=snapshot.revision,
                patch=summary_patch,
                idempotency_key=idem,
                client_message_id=client_msg,
                client_sequence=inputs.client_sequence,
                correlation_id=inputs.correlation_id,
            )
        except ConversationVersionConflict:
            # Retry ONCE with refreshed snapshot.
            reevaluations += 1
            if reevaluations > 1:
                raise OrchestratorRuntimeError(
                    "second version conflict during need-profile CAS",
                    reason_code="VERSION_CONFLICT_UNRECOVERABLE",
                )
            snapshot = await self._state.get_session(snapshot.session_id)
            result = await self._state.apply_model_update(
                snapshot.session_id,
                expected_revision=snapshot.revision,
                patch=summary_patch,
                idempotency_key=idem + ":retry",
                client_message_id=client_msg + ":retry",
                client_sequence=inputs.client_sequence,
                correlation_id=inputs.correlation_id,
            )
        revision = result.revision if result.revision is not None else snapshot.revision
        # Refresh so the caller sees the new revision.
        snapshot = await self._state.get_session(snapshot.session_id)
        return snapshot, revision, reevaluations

    async def _commit_category_resolution(
        self,
        snapshot: ConversationState,
        *,
        inputs: OrchestratorInputs,
        match_result: CategoryMatchResult,
        reevaluations: int,
    ) -> tuple[ConversationState, int, int]:
        idem = f"{inputs.idempotency_key}:cat"
        client_msg = f"{inputs.client_message_id}:cat"
        try:
            outcome = await self._applier.apply(
                session_id=snapshot.session_id,
                expected_revision=snapshot.revision,
                match_result=match_result,
                idempotency_key=idem,
                client_message_id=client_msg,
                client_sequence=inputs.client_sequence,
                correlation_id=inputs.correlation_id,
            )
        except ConversationVersionConflict:
            reevaluations += 1
            if reevaluations > 1:
                raise OrchestratorRuntimeError(
                    "second version conflict during category CAS",
                    reason_code="VERSION_CONFLICT_UNRECOVERABLE",
                )
            snapshot = await self._state.get_session(snapshot.session_id)
            outcome = await self._applier.apply(
                session_id=snapshot.session_id,
                expected_revision=snapshot.revision,
                match_result=match_result,
                idempotency_key=idem + ":retry",
                client_message_id=client_msg + ":retry",
                client_sequence=inputs.client_sequence,
                correlation_id=inputs.correlation_id,
            )
        if outcome.result is None or outcome.result.status is not CasStatus.APPLIED:
            # IDEMPOTENT_REPLAY / other non-terminal states: refresh + fall through.
            snapshot = await self._state.get_session(snapshot.session_id)
            return snapshot, snapshot.revision, reevaluations
        revision = (
            outcome.result.revision
            if outcome.result.revision is not None
            else snapshot.revision
        )
        snapshot = await self._state.get_session(snapshot.session_id)
        return snapshot, revision, reevaluations


__all__ = [
    "ChatOrchestrator",
    "ChatTurnOutcome",
    "OrchestratorInputs",
    "OrchestratorRuntimeError",
]

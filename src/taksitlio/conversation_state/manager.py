"""ConversationStateManager — sole owner of session mutations."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

from taksitlio.conversation_state.domain import (
    ActiveNeed,
    Actor,
    ActorType,
    CasStatus,
    ClarificationState,
    CompareAndSetResult,
    ConversationState,
    SessionStatus,
)
from taksitlio.conversation_state.errors import (
    ConversationOutOfOrder,
    ConversationPatchRejected,
    ConversationSessionExpired,
    ConversationSessionNotFound,
    ConversationVersionConflict,
    ConversationDuplicateRequest,
)
from taksitlio.conversation_state.events import (
    MetricsHook,
    NoOpConversationStateEventSink,
    NoOpMetricsHook,
    ConversationStateEventSink,
    make_state_changed_event,
    safe_log_update,
)
from taksitlio.conversation_state.patch_engine import PatchEngine
from taksitlio.conversation_state.policies import (
    ConversationStatePolicy,
    PolicyProvider,
    StaticPolicyProvider,
)
from taksitlio.conversation_state.repository import CasWriteRequest, ConversationStateRepository
from taksitlio.conversation_state.serialization import dumps_canonical, serialize_state


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fingerprint(parts: Mapping[str, Any]) -> str:
    raw = dumps_canonical(dict(parts))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ConversationStateManager:
    """
    Domain service above the repository.

    ModelRouter must never write state; orchestrator calls this manager only.
    """

    def __init__(
        self,
        repository: ConversationStateRepository,
        *,
        policies: PolicyProvider | None = None,
        patch_engine: PatchEngine | None = None,
        event_sink: ConversationStateEventSink | None = None,
        metrics: MetricsHook | None = None,
    ) -> None:
        self._repo = repository
        self._policies = policies or StaticPolicyProvider()
        self._patches = patch_engine or PatchEngine()
        self._events = event_sink or NoOpConversationStateEventSink()
        self._metrics = metrics or NoOpMetricsHook()

    async def create_session(
        self,
        *,
        locale: str = "tr-TR",
        actor: Actor | None = None,
        session_id: UUID | None = None,
        policy_code: str = "CONVERSATION_DEFAULT",
        idempotency_key: str | None = None,
    ) -> ConversationState:
        policy = await self._policies.get(policy_code)
        actor = actor or Actor(type=ActorType.ANONYMOUS)
        now = _utcnow()
        idle = (
            policy.authenticated_idle_ttl_seconds
            if actor.type == ActorType.AUTHENTICATED
            else policy.anonymous_idle_ttl_seconds
        )
        absolute = now + timedelta(seconds=policy.absolute_lifetime_seconds)
        expires = min(now + timedelta(seconds=idle), absolute)
        state = ConversationState(
            session_id=session_id or uuid4(),
            revision=0,
            status=SessionStatus.ACTIVE,
            locale=locale,
            actor=actor,
            created_at=now,
            updated_at=now,
            expires_at=expires,
            absolute_expires_at=absolute,
        )
        created = await self._repo.create(
            state,
            idle_ttl_seconds=idle,
            idempotency_key=idempotency_key,
            request_fingerprint=_fingerprint({"op": "create", "sid": str(state.session_id)})
            if idempotency_key
            else None,
        )
        self._metrics.incr("conversation_state_create_total")
        return created

    async def get_session(self, session_id: UUID) -> ConversationState:
        state = await self._repo.get(session_id)
        if state is None:
            raise ConversationSessionNotFound(str(session_id))
        if self._is_expired(state):
            raise ConversationSessionExpired(str(session_id))
        return state

    async def initialize_need(
        self,
        session_id: UUID,
        *,
        expected_revision: int,
        need: ActiveNeed | Mapping[str, Any],
        idempotency_key: str,
        client_message_id: str,
        client_sequence: int | None = None,
        correlation_id: str | None = None,
    ) -> CompareAndSetResult:
        snapshot = await self.get_session(session_id)
        active = need if isinstance(need, ActiveNeed) else ActiveNeed.from_dict(need)
        next_state = snapshot.copy()
        next_state.active_need = active
        next_state.status = SessionStatus.ACTIVE
        next_state.clarification = ClarificationState()
        return await self._commit(
            snapshot,
            next_state,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            client_message_id=client_message_id,
            client_sequence=client_sequence,
            correlation_id=correlation_id,
            operation_types=["INITIALIZE_NEED"],
        )

    async def apply_model_update(
        self,
        session_id: UUID,
        *,
        expected_revision: int,
        patch: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        idempotency_key: str,
        client_message_id: str,
        client_sequence: int | None = None,
        correlation_id: str | None = None,
    ) -> CompareAndSetResult:
        started = time.perf_counter()
        snapshot = await self.get_session(session_id)
        policy = await self._policies.get()
        patches = [patch] if isinstance(patch, Mapping) else list(patch)
        working = snapshot.copy()
        for item in patches:
            working = self._patches.apply(
                working,
                item,
                policy=policy,
                source_message_id=client_message_id,
            )

        # Platform fields owned by manager
        now = _utcnow()
        working.updated_at = now
        working.revision = snapshot.revision + 1
        working.last_client_message_id = client_message_id
        working.last_client_sequence = (
            client_sequence
            if client_sequence is not None
            else snapshot.last_client_sequence
        )
        working.actor = snapshot.actor
        working.session_id = snapshot.session_id
        working.schema_version = snapshot.schema_version
        working.created_at = snapshot.created_at
        working.absolute_expires_at = snapshot.absolute_expires_at
        working.expires_at = self._next_idle_expiry(snapshot.actor, policy, now, snapshot)

        if working.clarification.required:
            working.status = SessionStatus.AWAITING_CLARIFICATION
        elif working.status == SessionStatus.AWAITING_CLARIFICATION and not working.clarification.required:
            working.status = SessionStatus.ACTIVE

        result = await self._commit(
            snapshot,
            working,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            client_message_id=client_message_id,
            client_sequence=client_sequence,
            correlation_id=correlation_id,
            operation_types=[str(p.get("operation")) for p in patches],
            request_body={"patches": list(patches)},
        )
        duration_ms = (time.perf_counter() - started) * 1000.0
        self._metrics.observe("conversation_state_update_duration_ms", duration_ms)
        size = len(serialize_state(working).encode("utf-8"))
        self._metrics.observe("conversation_state_size_bytes", float(size))
        safe_log_update(
            correlation_id=correlation_id,
            revision=result.revision,
            operation_count=len(patches),
            decision=result.status.value,
            reason_code=None,
            duration_ms=duration_ms,
            serialized_size_bytes=size,
        )
        return result

    async def set_clarification(
        self,
        session_id: UUID,
        *,
        expected_revision: int,
        clarification: ClarificationState | Mapping[str, Any],
        idempotency_key: str,
        client_message_id: str,
        client_sequence: int | None = None,
        correlation_id: str | None = None,
    ) -> CompareAndSetResult:
        snapshot = await self.get_session(session_id)
        clar = (
            clarification
            if isinstance(clarification, ClarificationState)
            else ClarificationState.from_dict(clarification)
        )
        next_state = snapshot.copy()
        next_state.clarification = ClarificationState(
            required=True,
            reason_code=clar.reason_code,
            missing_concepts=clar.missing_concepts,
            question_intent=clar.question_intent,
            asked_at_revision=snapshot.revision,
        )
        next_state.status = SessionStatus.AWAITING_CLARIFICATION
        return await self._commit(
            snapshot,
            next_state,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            client_message_id=client_message_id,
            client_sequence=client_sequence,
            correlation_id=correlation_id,
            operation_types=["SET_CLARIFICATION"],
        )

    async def clear_clarification(
        self,
        session_id: UUID,
        *,
        expected_revision: int,
        idempotency_key: str,
        client_message_id: str,
        client_sequence: int | None = None,
        correlation_id: str | None = None,
    ) -> CompareAndSetResult:
        snapshot = await self.get_session(session_id)
        next_state = snapshot.copy()
        next_state.clarification = ClarificationState()
        next_state.status = SessionStatus.ACTIVE
        return await self._commit(
            snapshot,
            next_state,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            client_message_id=client_message_id,
            client_sequence=client_sequence,
            correlation_id=correlation_id,
            operation_types=["CLEAR_CLARIFICATION"],
        )

    async def mark_recommendation_ready(
        self,
        session_id: UUID,
        *,
        expected_revision: int,
        idempotency_key: str,
        client_message_id: str,
        client_sequence: int | None = None,
        correlation_id: str | None = None,
    ) -> CompareAndSetResult:
        snapshot = await self.get_session(session_id)
        next_state = snapshot.copy()
        next_state.status = SessionStatus.RECOMMENDATION_READY
        return await self._commit(
            snapshot,
            next_state,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            client_message_id=client_message_id,
            client_sequence=client_sequence,
            correlation_id=correlation_id,
            operation_types=["MARK_RECOMMENDATION_READY"],
        )

    async def complete_session(self, session_id: UUID) -> ConversationState:
        state = await self.get_session(session_id)
        return await self._repo.complete(session_id, state)

    async def reset_need(
        self,
        session_id: UUID,
        *,
        expected_revision: int,
        idempotency_key: str,
        client_message_id: str,
        seed: Mapping[str, Any] | None = None,
        client_sequence: int | None = None,
        correlation_id: str | None = None,
    ) -> CompareAndSetResult:
        patch: dict[str, Any] = {
            "operation": "RESET_NEED",
            "path": "/active_need",
            "confidence": 1.0,
        }
        if seed is not None:
            patch["value"] = dict(seed)
        return await self.apply_model_update(
            session_id,
            expected_revision=expected_revision,
            patch=patch,
            idempotency_key=idempotency_key,
            client_message_id=client_message_id,
            client_sequence=client_sequence,
            correlation_id=correlation_id,
        )

    async def touch_session(self, session_id: UUID) -> ConversationState:
        state = await self.get_session(session_id)
        policy = await self._policies.get()
        now = _utcnow()
        expires = self._next_idle_expiry(state.actor, policy, now, state)
        idle = (
            policy.authenticated_idle_ttl_seconds
            if state.actor.type == ActorType.AUTHENTICATED
            else policy.anonymous_idle_ttl_seconds
        )
        await self._repo.touch(
            session_id,
            expires_at_iso=expires.isoformat().replace("+00:00", "Z"),
            expires_at_epoch_ms=int(expires.timestamp() * 1000),
            idle_ttl_seconds=idle,
        )
        refreshed = await self._repo.get(session_id)
        assert refreshed is not None
        return refreshed

    async def _commit(
        self,
        snapshot: ConversationState,
        next_state: ConversationState,
        *,
        expected_revision: int,
        idempotency_key: str,
        client_message_id: str,
        client_sequence: int | None,
        correlation_id: str | None,
        operation_types: list[str],
        request_body: Mapping[str, Any] | None = None,
    ) -> CompareAndSetResult:
        policy = await self._policies.get()
        idle = (
            policy.authenticated_idle_ttl_seconds
            if snapshot.actor.type == ActorType.AUTHENTICATED
            else policy.anonymous_idle_ttl_seconds
        )
        now = _utcnow()
        next_state.updated_at = now
        next_state.revision = snapshot.revision + 1
        next_state.last_client_message_id = client_message_id
        if client_sequence is not None:
            next_state.last_client_sequence = client_sequence
        next_state.expires_at = self._next_idle_expiry(
            snapshot.actor, policy, now, snapshot
        )
        next_state.absolute_expires_at = snapshot.absolute_expires_at
        next_state.actor = snapshot.actor
        next_state.created_at = snapshot.created_at
        next_state.session_id = snapshot.session_id

        fp = _fingerprint(
            {
                "session_id": str(snapshot.session_id),
                "expected_revision": expected_revision,
                "client_message_id": client_message_id,
                "client_sequence": client_sequence,
                "body": request_body or {"ops": operation_types},
            }
        )
        # Never log idempotency_key — fingerprint only internally
        result = await self._repo.compare_and_set(
            CasWriteRequest(
                session_id=snapshot.session_id,
                expected_revision=expected_revision,
                next_state=next_state,
                idempotency_key=idempotency_key,
                client_message_id=client_message_id,
                client_sequence=client_sequence,
                request_fingerprint=fp,
                idle_ttl_seconds=idle,
                idempotency_ttl_seconds=max(
                    policy.idempotency_ttl_seconds, idle
                ),
            )
        )
        self._metrics.incr("conversation_state_update_total")
        if result.status == CasStatus.APPLIED:
            try:
                await self._events.publish(
                    make_state_changed_event(
                        session_id=snapshot.session_id,
                        previous_revision=snapshot.revision,
                        new_revision=result.revision or next_state.revision,
                        event_type="STATE_UPDATED",
                        operation_types=operation_types,
                        correlation_id=correlation_id,
                    )
                )
            except Exception:  # noqa: BLE001 — sink failure must not roll back
                self._metrics.incr("conversation_redis_errors_total")
        elif result.status == CasStatus.IDEMPOTENT_REPLAY:
            self._metrics.incr("conversation_state_idempotent_replay_total")
        elif result.status == CasStatus.VERSION_CONFLICT:
            self._metrics.incr("conversation_state_conflict_total")
            raise ConversationVersionConflict(
                expected_revision=expected_revision,
                actual_revision=result.revision,
            )
        elif result.status == CasStatus.OUT_OF_ORDER:
            self._metrics.incr("conversation_state_out_of_order_total")
            raise ConversationOutOfOrder(result.detail or "out of order")
        elif result.status == CasStatus.SESSION_EXPIRED:
            self._metrics.incr("conversation_state_expired_total")
            raise ConversationSessionExpired(str(snapshot.session_id))
        elif result.status == CasStatus.SESSION_NOT_FOUND:
            raise ConversationSessionNotFound(str(snapshot.session_id))
        elif result.status == CasStatus.DUPLICATE_PAYLOAD_MISMATCH:
            raise ConversationDuplicateRequest(
                "Idempotency key reused with different payload"
            )
        elif result.status == CasStatus.INVALID_STATE:
            self._metrics.incr("conversation_state_patch_rejected_total")
            raise ConversationPatchRejected(result.detail or "invalid state")
        return result

    @staticmethod
    def _is_expired(state: ConversationState) -> bool:
        now = _utcnow()
        if state.status in {
            SessionStatus.EXPIRED,
            SessionStatus.COMPLETED,
            SessionStatus.CANCELLED,
        }:
            return state.status == SessionStatus.EXPIRED
        return now >= state.absolute_expires_at or now >= state.expires_at

    @staticmethod
    def _next_idle_expiry(
        actor: Actor,
        policy: ConversationStatePolicy,
        now: datetime,
        state: ConversationState,
    ) -> datetime:
        idle = (
            policy.authenticated_idle_ttl_seconds
            if actor.type == ActorType.AUTHENTICATED
            else policy.anonymous_idle_ttl_seconds
        )
        candidate = now + timedelta(seconds=idle)
        return min(candidate, state.absolute_expires_at)

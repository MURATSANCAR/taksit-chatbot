"""In-memory repository — unit tests only; never production fallback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from taksitlio.conversation_state.domain import (
    CasStatus,
    CompareAndSetResult,
    ConversationState,
    SessionStatus,
)
from taksitlio.conversation_state.errors import ConversationSessionExists
from taksitlio.conversation_state.repository import CasWriteRequest, ConversationStateRepository
from taksitlio.conversation_state.serialization import serialize_state


@dataclass
class _IdemRecord:
    fingerprint: str
    revision: int
    result_revision: int
    client_message_id: str


class InMemoryConversationStateRepository:
    """Simulates Redis CAS / idempotency / ordering semantics."""

    def __init__(self) -> None:
        self._states: dict[str, ConversationState] = {}
        self._idem: dict[str, _IdemRecord] = {}
        self._lock = asyncio.Lock()

    def _sid(self, session_id: UUID) -> str:
        return str(session_id)

    async def create(
        self,
        state: ConversationState,
        *,
        idle_ttl_seconds: int,
        idempotency_key: str | None = None,
        request_fingerprint: str | None = None,
    ) -> ConversationState:
        async with self._lock:
            key = self._sid(state.session_id)
            if key in self._states:
                raise ConversationSessionExists(f"Session already exists: {key}")
            stored = state.copy()
            stored.revision = 0
            self._states[key] = stored
            if idempotency_key and request_fingerprint:
                self._idem[f"{key}:{idempotency_key}"] = _IdemRecord(
                    fingerprint=request_fingerprint,
                    revision=0,
                    result_revision=0,
                    client_message_id=stored.last_client_message_id or "",
                )
            return stored.copy()

    async def get(self, session_id: UUID) -> ConversationState | None:
        async with self._lock:
            state = self._states.get(self._sid(session_id))
            return state.copy() if state else None

    async def compare_and_set(self, request: CasWriteRequest) -> CompareAndSetResult:
        async with self._lock:
            key = self._sid(request.session_id)
            idem_key = f"{key}:{request.idempotency_key}"

            if idem_key in self._idem:
                rec = self._idem[idem_key]
                if rec.fingerprint == request.request_fingerprint:
                    state = self._states.get(key)
                    return CompareAndSetResult(
                        status=CasStatus.IDEMPOTENT_REPLAY,
                        session_id=request.session_id,
                        revision=rec.result_revision,
                        client_message_id=request.client_message_id,
                        state=state.copy() if state else None,
                    )
                return CompareAndSetResult(
                    status=CasStatus.DUPLICATE_PAYLOAD_MISMATCH,
                    session_id=request.session_id,
                    revision=rec.result_revision,
                    client_message_id=request.client_message_id,
                    detail="Same idempotency key with different payload",
                )

            current = self._states.get(key)
            if current is None:
                return CompareAndSetResult(
                    status=CasStatus.SESSION_NOT_FOUND,
                    session_id=request.session_id,
                )

            now = datetime.now(timezone.utc)
            if current.status in {
                SessionStatus.EXPIRED,
                SessionStatus.COMPLETED,
                SessionStatus.CANCELLED,
            }:
                return CompareAndSetResult(
                    status=CasStatus.SESSION_EXPIRED,
                    session_id=request.session_id,
                    revision=current.revision,
                    state=current.copy(),
                )
            if now >= current.absolute_expires_at or now >= current.expires_at:
                current.status = SessionStatus.EXPIRED
                return CompareAndSetResult(
                    status=CasStatus.SESSION_EXPIRED,
                    session_id=request.session_id,
                    revision=current.revision,
                    state=current.copy(),
                )

            if current.revision != request.expected_revision:
                return CompareAndSetResult(
                    status=CasStatus.VERSION_CONFLICT,
                    session_id=request.session_id,
                    revision=current.revision,
                    state=current.copy(),
                    detail=f"expected={request.expected_revision} actual={current.revision}",
                )

            if (
                request.client_sequence is not None
                and current.last_client_sequence is not None
            ):
                if request.client_sequence < current.last_client_sequence:
                    return CompareAndSetResult(
                        status=CasStatus.OUT_OF_ORDER,
                        session_id=request.session_id,
                        revision=current.revision,
                        state=current.copy(),
                    )
                if request.client_sequence == current.last_client_sequence:
                    return CompareAndSetResult(
                        status=CasStatus.OUT_OF_ORDER,
                        session_id=request.session_id,
                        revision=current.revision,
                        state=current.copy(),
                        detail="duplicate sequence without matching idempotency",
                    )

            next_state = request.next_state.copy()
            if next_state.revision != current.revision + 1:
                return CompareAndSetResult(
                    status=CasStatus.INVALID_STATE,
                    session_id=request.session_id,
                    revision=current.revision,
                    detail="next revision must be current+1",
                )

            # Force-serialize check (mirrors Redis size constraints upstream)
            serialize_state(next_state)

            self._states[key] = next_state
            self._idem[idem_key] = _IdemRecord(
                fingerprint=request.request_fingerprint,
                revision=next_state.revision,
                result_revision=next_state.revision,
                client_message_id=request.client_message_id,
            )
            return CompareAndSetResult(
                status=CasStatus.APPLIED,
                session_id=request.session_id,
                revision=next_state.revision,
                client_message_id=request.client_message_id,
                state=next_state.copy(),
            )

    async def touch(
        self,
        session_id: UUID,
        *,
        expires_at_iso: str,
        expires_at_epoch_ms: int,
        idle_ttl_seconds: int,
    ) -> bool:
        async with self._lock:
            state = self._states.get(self._sid(session_id))
            if state is None:
                return False
            from taksitlio.conversation_state.domain import _parse_dt

            state.expires_at = _parse_dt(expires_at_iso)
            state.updated_at = datetime.now(timezone.utc)
            return True

    async def complete(
        self, session_id: UUID, state: ConversationState
    ) -> ConversationState:
        async with self._lock:
            key = self._sid(session_id)
            stored = state.copy()
            stored.status = SessionStatus.COMPLETED
            self._states[key] = stored
            return stored.copy()

    async def delete(self, session_id: UUID) -> None:
        async with self._lock:
            key = self._sid(session_id)
            self._states.pop(key, None)
            prefix = f"{key}:"
            for idem in list(self._idem):
                if idem.startswith(prefix):
                    self._idem.pop(idem, None)

    async def exists(self, session_id: UUID) -> bool:
        async with self._lock:
            return self._sid(session_id) in self._states

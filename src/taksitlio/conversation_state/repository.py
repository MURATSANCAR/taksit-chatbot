"""Conversation state repository protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from taksitlio.conversation_state.domain import CasStatus, CompareAndSetResult, ConversationState


@dataclass(frozen=True)
class CasWriteRequest:
    session_id: UUID
    expected_revision: int
    next_state: ConversationState
    idempotency_key: str
    client_message_id: str
    client_sequence: int | None
    request_fingerprint: str
    idle_ttl_seconds: int
    idempotency_ttl_seconds: int


class ConversationStateRepository(Protocol):
    async def create(
        self,
        state: ConversationState,
        *,
        idle_ttl_seconds: int,
        idempotency_key: str | None = None,
        request_fingerprint: str | None = None,
    ) -> ConversationState: ...

    async def get(self, session_id: UUID) -> ConversationState | None: ...

    async def compare_and_set(self, request: CasWriteRequest) -> CompareAndSetResult: ...

    async def touch(
        self,
        session_id: UUID,
        *,
        expires_at_iso: str,
        expires_at_epoch_ms: int,
        idle_ttl_seconds: int,
    ) -> bool: ...

    async def complete(self, session_id: UUID, state: ConversationState) -> ConversationState: ...

    async def delete(self, session_id: UUID) -> None: ...

    async def exists(self, session_id: UUID) -> bool: ...

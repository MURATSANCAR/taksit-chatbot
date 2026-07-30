"""Redis-backed conversation state repository with Lua CAS."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from taksitlio.conversation_state.domain import (
    CasStatus,
    CompareAndSetResult,
    ConversationState,
    SessionStatus,
)
from taksitlio.conversation_state.errors import (
    ConversationRepositoryUnavailable,
    ConversationSessionExists,
)
from taksitlio.conversation_state.repository import CasWriteRequest
from taksitlio.conversation_state.serialization import deserialize_state, serialize_state


def state_key(session_id: UUID, *, prefix: str = "taksitlio") -> str:
    return f"{prefix}:chat:{{{session_id}}}:state"


def idempotency_key_digest(idempotency_key: str) -> str:
    """SHA-256 digest for Redis key material — never store raw external keys."""
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()


def idem_key(session_id: UUID, idempotency_key: str, *, prefix: str = "taksitlio") -> str:
    digest = idempotency_key_digest(idempotency_key)
    return f"{prefix}:chat:{{{session_id}}}:idem:{digest}"


def events_key(session_id: UUID, *, prefix: str = "taksitlio") -> str:
    return f"{prefix}:chat:{{{session_id}}}:events"


def _load_lua() -> str:
    path = Path(__file__).resolve().parent / "lua" / "compare_and_set.lua"
    return path.read_text(encoding="utf-8")


def _epoch_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


class RedisConversationStateRepository:
    def __init__(
        self,
        redis: Any,
        *,
        key_prefix: str = "taksitlio",
        max_retries: int = 1,
    ) -> None:
        self._redis = redis
        self._prefix = key_prefix
        self._max_retries = max(0, max_retries)
        self._lua = _load_lua()
        self._script = None

    async def _eval(self, *args: Any) -> Any:
        try:
            if self._script is None:
                # redis.asyncio: register script once
                self._script = self._redis.register_script(self._lua)
            return await self._script(keys=args[0], args=args[1])
        except Exception as exc:  # noqa: BLE001 — map to typed infra error
            raise ConversationRepositoryUnavailable(str(exc)) from exc

    async def create(
        self,
        state: ConversationState,
        *,
        idle_ttl_seconds: int,
        idempotency_key: str | None = None,
        request_fingerprint: str | None = None,
    ) -> ConversationState:
        key = state_key(state.session_id, prefix=self._prefix)
        payload = serialize_state(state)
        mapping = {
            "payload": payload,
            "revision": str(state.revision),
            "schema_version": state.schema_version,
            "status": state.status.value,
            "created_at": state.created_at.isoformat().replace("+00:00", "Z"),
            "updated_at": state.updated_at.isoformat().replace("+00:00", "Z"),
            "expires_at": state.expires_at.isoformat().replace("+00:00", "Z"),
            "absolute_expires_at": state.absolute_expires_at.isoformat().replace(
                "+00:00", "Z"
            ),
            "expires_at_epoch_ms": str(_epoch_ms(state.expires_at)),
            "absolute_expires_at_epoch_ms": str(_epoch_ms(state.absolute_expires_at)),
            "last_client_message_id": state.last_client_message_id or "",
            "last_client_sequence": (
                "" if state.last_client_sequence is None else str(state.last_client_sequence)
            ),
        }
        try:
            if await self._redis.exists(key):
                raise ConversationSessionExists(str(state.session_id))
            await self._redis.hset(key, mapping=mapping)
            ttl_ms = max(1000, idle_ttl_seconds * 1000)
            abs_left = _epoch_ms(state.absolute_expires_at) - _epoch_ms(
                datetime.now(timezone.utc)
            )
            if abs_left > 0:
                ttl_ms = min(ttl_ms, abs_left)
            await self._redis.pexpire(key, int(ttl_ms))
        except ConversationSessionExists:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConversationRepositoryUnavailable(str(exc)) from exc
        return state

    async def get(self, session_id: UUID) -> ConversationState | None:
        key = state_key(session_id, prefix=self._prefix)
        try:
            raw = await self._redis.hget(key, "payload")
        except Exception as exc:  # noqa: BLE001
            raise ConversationRepositoryUnavailable(str(exc)) from exc
        if raw is None:
            return None
        return deserialize_state(raw)

    async def compare_and_set(self, request: CasWriteRequest) -> CompareAndSetResult:
        s_key = state_key(request.session_id, prefix=self._prefix)
        i_key = idem_key(
            request.session_id, request.idempotency_key, prefix=self._prefix
        )
        next_state = request.next_state
        payload = serialize_state(next_state)
        now = datetime.now(timezone.utc)
        now_ms = _epoch_ms(now)
        seq = (
            ""
            if request.client_sequence is None
            else str(request.client_sequence)
        )
        idem_result = (
            f'{{"status":"APPLIED","revision":{next_state.revision},'
            f'"client_message_id":"{request.client_message_id}"}}'
        )
        argv = [
            str(request.expected_revision),
            str(next_state.revision),
            payload,
            next_state.schema_version,
            next_state.status.value,
            next_state.updated_at.isoformat().replace("+00:00", "Z"),
            next_state.expires_at.isoformat().replace("+00:00", "Z"),
            next_state.absolute_expires_at.isoformat().replace("+00:00", "Z"),
            request.client_message_id,
            seq,
            str(now_ms),
            str(request.idle_ttl_seconds),
            str(request.idempotency_ttl_seconds),
            request.client_message_id,
            request.request_fingerprint,
            idem_result,
            str(_epoch_ms(next_state.expires_at)),
            str(_epoch_ms(next_state.absolute_expires_at)),
        ]
        try:
            if self._script is None:
                self._script = self._redis.register_script(self._lua)
            result = await self._script(keys=[s_key, i_key], args=argv)
        except Exception as exc:  # noqa: BLE001
            raise ConversationRepositoryUnavailable(str(exc)) from exc

        status_raw = result[0]
        if isinstance(status_raw, bytes):
            status_raw = status_raw.decode("utf-8")
        rev_raw = result[1]
        if isinstance(rev_raw, bytes):
            rev_raw = rev_raw.decode("utf-8")
        status = CasStatus(str(status_raw))
        revision = int(rev_raw) if rev_raw not in {"", None} else None
        state = None
        if status in {CasStatus.APPLIED, CasStatus.IDEMPOTENT_REPLAY, CasStatus.VERSION_CONFLICT}:
            state = await self.get(request.session_id)
        return CompareAndSetResult(
            status=status,
            session_id=request.session_id,
            revision=revision,
            client_message_id=request.client_message_id,
            state=state,
        )

    async def touch(
        self,
        session_id: UUID,
        *,
        expires_at_iso: str,
        expires_at_epoch_ms: int,
        idle_ttl_seconds: int,
    ) -> bool:
        key = state_key(session_id, prefix=self._prefix)
        try:
            if not await self._redis.exists(key):
                return False
            await self._redis.hset(
                key,
                mapping={
                    "expires_at": expires_at_iso,
                    "expires_at_epoch_ms": str(expires_at_epoch_ms),
                    "updated_at": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                },
            )
            abs_epoch = int(
                (await self._redis.hget(key, "absolute_expires_at_epoch_ms")) or 0
            )
            now_ms = _epoch_ms(datetime.now(timezone.utc))
            ttl_ms = idle_ttl_seconds * 1000
            if abs_epoch > 0:
                ttl_ms = min(ttl_ms, max(1000, abs_epoch - now_ms))
            await self._redis.pexpire(key, int(ttl_ms))
            return True
        except Exception as exc:  # noqa: BLE001
            raise ConversationRepositoryUnavailable(str(exc)) from exc

    async def complete(
        self, session_id: UUID, state: ConversationState
    ) -> ConversationState:
        completed = state.copy()
        completed.status = SessionStatus.COMPLETED
        key = state_key(session_id, prefix=self._prefix)
        try:
            await self._redis.hset(
                key,
                mapping={
                    "payload": serialize_state(completed),
                    "status": SessionStatus.COMPLETED.value,
                    "revision": str(completed.revision),
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise ConversationRepositoryUnavailable(str(exc)) from exc
        return completed

    async def delete(self, session_id: UUID) -> None:
        key = state_key(session_id, prefix=self._prefix)
        try:
            await self._redis.delete(key)
        except Exception as exc:  # noqa: BLE001
            raise ConversationRepositoryUnavailable(str(exc)) from exc

    async def exists(self, session_id: UUID) -> bool:
        try:
            return bool(await self._redis.exists(state_key(session_id, prefix=self._prefix)))
        except Exception as exc:  # noqa: BLE001
            raise ConversationRepositoryUnavailable(str(exc)) from exc

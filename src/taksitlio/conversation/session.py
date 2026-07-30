"""Redis-backed conversation / session state manager."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from redis.asyncio import Redis

from taksitlio.conversation.state import apply_conversation_update


@dataclass
class SessionState:
    session_id: str
    user_id: str | None = None
    need_profile: dict[str, Any] | None = None
    matched_category_codes: list[str] = field(default_factory=list)
    last_clarification_intent: str | None = None
    turn_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)

    def summary(self) -> dict[str, Any]:
        """Compact summary sent to FAST model — not full chat history."""
        return {
            "need_profile": self.need_profile,
            "matched_category_codes": self.matched_category_codes,
            "last_clarification_intent": self.last_clarification_intent,
            "turn_count": self.turn_count,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionState":
        return cls(
            session_id=str(data["session_id"]),
            user_id=data.get("user_id"),
            need_profile=data.get("need_profile"),
            matched_category_codes=list(data.get("matched_category_codes") or []),
            last_clarification_intent=data.get("last_clarification_intent"),
            turn_count=int(data.get("turn_count") or 0),
            metadata=dict(data.get("metadata") or {}),
            updated_at=float(data.get("updated_at") or time.time()),
        )


class SessionStore(Protocol):
    async def get(self, session_id: str) -> SessionState | None: ...

    async def save(self, state: SessionState) -> None: ...

    async def delete(self, session_id: str) -> None: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._data: dict[str, SessionState] = {}

    async def get(self, session_id: str) -> SessionState | None:
        state = self._data.get(session_id)
        return SessionState.from_dict(state.to_dict()) if state else None

    async def save(self, state: SessionState) -> None:
        state.updated_at = time.time()
        self._data[state.session_id] = SessionState.from_dict(state.to_dict())

    async def delete(self, session_id: str) -> None:
        self._data.pop(session_id, None)


class RedisSessionStore:
    def __init__(
        self,
        redis: Redis,
        *,
        key_prefix: str = "taksitlio",
        ttl_seconds: int = 86400,
    ) -> None:
        self._redis = redis
        self._prefix = key_prefix.rstrip(":")
        self._ttl = ttl_seconds

    def _key(self, session_id: str) -> str:
        return f"{self._prefix}:session:{session_id}"

    async def get(self, session_id: str) -> SessionState | None:
        raw = await self._redis.get(self._key(session_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return SessionState.from_dict(json.loads(raw))

    async def save(self, state: SessionState) -> None:
        state.updated_at = time.time()
        await self._redis.set(
            self._key(state.session_id),
            json.dumps(state.to_dict(), ensure_ascii=False),
            ex=self._ttl,
        )

    async def delete(self, session_id: str) -> None:
        await self._redis.delete(self._key(session_id))


class ConversationStateManager:
    """Loads/saves structured session need state; applies UPDATE operations."""

    def __init__(self, store: SessionStore) -> None:
        self._store = store

    async def get_or_create(
        self,
        session_id: str,
        *,
        user_id: str | None = None,
    ) -> SessionState:
        state = await self._store.get(session_id)
        if state is None:
            state = SessionState(session_id=session_id, user_id=user_id)
            await self._store.save(state)
        elif user_id and not state.user_id:
            state.user_id = user_id
            await self._store.save(state)
        return state

    async def apply_need_profile(
        self,
        session_id: str,
        need_profile: dict[str, Any],
        *,
        category_codes: list[str] | None = None,
        clarification_intent: str | None = None,
    ) -> SessionState:
        state = await self.get_or_create(session_id)
        state.need_profile = need_profile
        state.turn_count += 1
        if category_codes is not None:
            state.matched_category_codes = list(category_codes)
        if clarification_intent is not None:
            state.last_clarification_intent = clarification_intent
        await self._store.save(state)
        return state

    async def apply_update(
        self,
        session_id: str,
        update: dict[str, Any],
    ) -> SessionState:
        state = await self.get_or_create(session_id)
        state.need_profile = apply_conversation_update(state.need_profile, update)
        state.turn_count += 1
        await self._store.save(state)
        return state

    async def reset(self, session_id: str) -> None:
        await self._store.delete(session_id)

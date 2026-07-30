"""Conversation state policies (loaded from DB or static defaults for tests)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from taksitlio.conversation_state.errors import ConversationPolicyNotFound


@dataclass(frozen=True)
class ConversationStatePolicy:
    policy_code: str
    display_name: str
    anonymous_idle_ttl_seconds: int = 1800
    authenticated_idle_ttl_seconds: int = 86400
    absolute_lifetime_seconds: int = 604800
    idempotency_ttl_seconds: int = 604800
    max_state_size_bytes: int = 65536
    max_preferences: int = 32
    max_entities: int = 32
    max_ambiguities: int = 16
    max_category_candidates: int = 8
    max_metadata_bytes: int = 4096
    max_string_length: int = 500
    status: str = "ACTIVE"


DEFAULT_POLICY = ConversationStatePolicy(
    policy_code="CONVERSATION_DEFAULT",
    display_name="Varsayılan conversation state politikası",
)


class PolicyProvider(Protocol):
    async def get(self, policy_code: str = "CONVERSATION_DEFAULT") -> ConversationStatePolicy: ...


class StaticPolicyProvider:
    def __init__(self, policy: ConversationStatePolicy | None = None) -> None:
        self._policy = policy or DEFAULT_POLICY

    async def get(self, policy_code: str = "CONVERSATION_DEFAULT") -> ConversationStatePolicy:
        if policy_code != self._policy.policy_code and policy_code != "CONVERSATION_DEFAULT":
            raise ConversationPolicyNotFound(f"Unknown policy: {policy_code}")
        return self._policy

"""ADR-006 §C: semantic_constraints patch surface + RESET_NEED clearing."""

from __future__ import annotations

import pytest

from taksitlio.conversation_state.errors import (
    ConversationPatchRejected,
    ConversationStateTooLarge,
)
from taksitlio.conversation_state.events import (
    InMemoryConversationStateEventSink,
    InMemoryMetricsHook,
)
from taksitlio.conversation_state.in_memory_repository import (
    InMemoryConversationStateRepository,
)
from taksitlio.conversation_state.manager import ConversationStateManager
from taksitlio.conversation_state.policies import (
    ConversationStatePolicy,
    DEFAULT_POLICY,
    StaticPolicyProvider,
)


def _manager(policy: ConversationStatePolicy | None = None) -> ConversationStateManager:
    provider = StaticPolicyProvider(policy or DEFAULT_POLICY)
    return ConversationStateManager(
        InMemoryConversationStateRepository(),
        event_sink=InMemoryConversationStateEventSink(),
        metrics=InMemoryMetricsHook(),
        policies=provider,
    )


@pytest.mark.asyncio
async def test_semantic_constraints_can_be_set_and_survive_apply():
    mgr = _manager()
    state = await mgr.create_session()
    await mgr.initialize_need(
        state.session_id,
        expected_revision=0,
        idempotency_key="init-1",
        client_message_id="m1",
        need={"need_description": "telefon lazım"},
    )
    result = await mgr.apply_model_update(
        state.session_id,
        expected_revision=1,
        idempotency_key="pu-1",
        client_message_id="m2",
        patch={
            "operation": "SET",
            "path": "/active_need/semantic_constraints",
            "value": {
                "positive": [{"concept": "laptop", "provenance": "EXPLICIT"}],
                "negative": [
                    {"concept": "telefon", "provenance": "EXPLICIT_NEGATION"}
                ],
                "corrections": [],
            },
        },
    )
    updated = await mgr.get_session(state.session_id)
    assert result.status.value == "APPLIED"
    concepts_pos = [c.concept for c in updated.active_need.semantic_constraints.positive]
    concepts_neg = [c.concept for c in updated.active_need.semantic_constraints.negative]
    assert concepts_pos == ["laptop"]
    assert concepts_neg == ["telefon"]


@pytest.mark.asyncio
async def test_reset_need_clears_semantic_constraints():
    mgr = _manager()
    state = await mgr.create_session()
    await mgr.initialize_need(
        state.session_id,
        expected_revision=0,
        idempotency_key="init-2",
        client_message_id="m1",
        need={"need_description": "telefon"},
    )
    await mgr.apply_model_update(
        state.session_id,
        expected_revision=1,
        idempotency_key="pu-2",
        client_message_id="m2",
        patch={
            "operation": "SET",
            "path": "/active_need/semantic_constraints",
            "value": {
                "positive": [{"concept": "laptop", "provenance": "EXPLICIT"}],
                "negative": [],
                "corrections": [],
            },
        },
    )
    await mgr.reset_need(
        state.session_id,
        expected_revision=2,
        idempotency_key="rn-1",
        client_message_id="m3",
        seed={"need_description": "yeni ihtiyaç"},
    )
    updated = await mgr.get_session(state.session_id)
    assert updated.active_need.semantic_constraints.positive == ()
    assert updated.active_need.semantic_constraints.negative == ()
    assert updated.active_need.semantic_constraints.corrections == ()


@pytest.mark.asyncio
async def test_constraint_limits_enforced_by_policy():
    from dataclasses import replace

    policy = replace(
        DEFAULT_POLICY,
        max_positive_constraints=2,
        max_negative_constraints=2,
        max_corrections=1,
    )
    mgr = _manager(policy=policy)
    state = await mgr.create_session()
    await mgr.initialize_need(
        state.session_id,
        expected_revision=0,
        idempotency_key="init-3",
        client_message_id="m1",
        need={"need_description": "telefon"},
    )
    with pytest.raises(ConversationStateTooLarge):
        await mgr.apply_model_update(
            state.session_id,
            expected_revision=1,
            idempotency_key="pu-3",
            client_message_id="m2",
            patch={
                "operation": "SET",
                "path": "/active_need/semantic_constraints",
                "value": {
                    "positive": [
                        {"concept": f"c{i}", "provenance": "EXPLICIT"}
                        for i in range(5)
                    ],
                    "negative": [],
                    "corrections": [],
                },
            },
        )

"""Extra coverage for RESET_NEED schema hardening.

These tests are separate from `test_manager.py` so they can be run in
isolation. They exercise the JSON-schema surface applied by
`PatchEngine._validate_need_seed`.
"""

from __future__ import annotations

import pytest

from taksitlio.conversation_state.errors import ConversationPatchRejected
from taksitlio.conversation_state.events import (
    InMemoryConversationStateEventSink,
    InMemoryMetricsHook,
)
from taksitlio.conversation_state.in_memory_repository import (
    InMemoryConversationStateRepository,
)
from taksitlio.conversation_state.manager import ConversationStateManager


def _manager() -> ConversationStateManager:
    return ConversationStateManager(
        InMemoryConversationStateRepository(),
        event_sink=InMemoryConversationStateEventSink(),
        metrics=InMemoryMetricsHook(),
    )


@pytest.mark.asyncio
async def test_reset_need_rejects_unknown_top_level_field():
    mgr = _manager()
    state = await mgr.create_session()
    with pytest.raises(ConversationPatchRejected):
        await mgr.reset_need(
            state.session_id,
            expected_revision=0,
            idempotency_key="rn-x",
            client_message_id="mx",
            seed={"need_description": "hi", "unknown_field": "boom"},
        )


@pytest.mark.asyncio
async def test_reset_need_rejects_bad_intent_type():
    mgr = _manager()
    state = await mgr.create_session()
    with pytest.raises(ConversationPatchRejected):
        await mgr.reset_need(
            state.session_id,
            expected_revision=0,
            idempotency_key="rn-y",
            client_message_id="my",
            seed={
                "need_description": "telefon",
                "intent": {"type": "NOT_A_REAL_INTENT", "confidence": 0.9},
            },
        )


@pytest.mark.asyncio
async def test_reset_need_rejects_budget_currency_outside_enum():
    mgr = _manager()
    state = await mgr.create_session()
    with pytest.raises(ConversationPatchRejected):
        await mgr.reset_need(
            state.session_id,
            expected_revision=0,
            idempotency_key="rn-z",
            client_message_id="mz",
            seed={
                "need_description": "telefon",
                "budget": {"type": "EXACT", "value": 40000, "currency": "USD"},
            },
        )


@pytest.mark.asyncio
async def test_reset_need_rejects_platform_field_in_seed():
    mgr = _manager()
    state = await mgr.create_session()
    with pytest.raises(ConversationPatchRejected):
        await mgr.reset_need(
            state.session_id,
            expected_revision=0,
            idempotency_key="rn-p",
            client_message_id="mp",
            seed={"need_description": "telefon", "revision": 99},
        )

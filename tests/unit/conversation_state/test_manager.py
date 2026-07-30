"""Unit tests for Conversation State Manager + optimistic locking."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from taksitlio.conversation_state.domain import (
    ActiveNeed,
    Actor,
    ActorType,
    CasStatus,
    ClarificationState,
    SessionStatus,
)
from taksitlio.conversation_state.errors import (
    ConversationDuplicateRequest,
    ConversationOutOfOrder,
    ConversationPatchRejected,
    ConversationSessionExists,
    ConversationSessionExpired,
    ConversationSessionNotFound,
    ConversationStateTooLarge,
    ConversationVersionConflict,
)
from taksitlio.conversation_state.events import InMemoryConversationStateEventSink, InMemoryMetricsHook
from taksitlio.conversation_state.in_memory_repository import InMemoryConversationStateRepository
from taksitlio.conversation_state.manager import ConversationStateManager
from taksitlio.conversation_state.orchestrator_bridge import DefaultOrchestratorBridge
from taksitlio.conversation_state.policies import ConversationStatePolicy, StaticPolicyProvider
from taksitlio.conversation_state.redis_repository import idem_key, state_key


def _mgr(**kwargs) -> ConversationStateManager:
    return ConversationStateManager(
        InMemoryConversationStateRepository(),
        event_sink=InMemoryConversationStateEventSink(),
        metrics=InMemoryMetricsHook(),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_create_session_revision_zero_and_duplicate_create():
    mgr = _mgr()
    sid = uuid4()
    state = await mgr.create_session(session_id=sid)
    assert state.revision == 0
    assert state.status == SessionStatus.ACTIVE
    with pytest.raises(ConversationSessionExists):
        await mgr.create_session(session_id=sid)


@pytest.mark.asyncio
async def test_get_and_complete_session():
    mgr = _mgr()
    created = await mgr.create_session()
    loaded = await mgr.get_session(created.session_id)
    assert loaded.session_id == created.session_id
    completed = await mgr.complete_session(created.session_id)
    assert completed.status == SessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_optimistic_locking_apply_and_conflict():
    mgr = _mgr()
    state = await mgr.create_session()
    result = await mgr.apply_model_update(
        state.session_id,
        expected_revision=0,
        patch={
            "operation": "SET",
            "path": "/active_need/need_description",
            "value": "hafif cihaz",
            "confidence": 0.9,
            "evidence_text": "secret evidence must not be logged",
        },
        idempotency_key="idem-1",
        client_message_id="msg-1",
        client_sequence=1,
    )
    assert result.status == CasStatus.APPLIED
    assert result.revision == 1
    assert result.state is not None
    assert result.state.active_need is not None
    assert result.state.active_need.need_description == "hafif cihaz"

    with pytest.raises(ConversationVersionConflict):
        await mgr.apply_model_update(
            state.session_id,
            expected_revision=0,
            patch={
                "operation": "SET",
                "path": "/active_need/need_description",
                "value": "should fail",
                "confidence": 0.9,
            },
            idempotency_key="idem-2",
            client_message_id="msg-2",
            client_sequence=2,
        )
    # State unchanged at revision 1
    current = await mgr.get_session(state.session_id)
    assert current.revision == 1
    assert current.active_need.need_description == "hafif cihaz"


@pytest.mark.asyncio
async def test_concurrent_updates_only_one_wins():
    mgr = _mgr()
    state = await mgr.create_session()

    async def attempt(i: int):
        try:
            return await mgr.apply_model_update(
                state.session_id,
                expected_revision=0,
                patch={
                    "operation": "SET",
                    "path": "/active_need/need_description",
                    "value": f"v{i}",
                    "confidence": 0.9,
                },
                idempotency_key=f"idem-{i}",
                client_message_id=f"msg-{i}",
                client_sequence=i + 1,
            )
        except ConversationVersionConflict:
            return None

    results = await asyncio.gather(*[attempt(i) for i in range(10)])
    applied = [r for r in results if r is not None and r.status == CasStatus.APPLIED]
    assert len(applied) == 1
    current = await mgr.get_session(state.session_id)
    assert current.revision == 1


@pytest.mark.asyncio
async def test_idempotency_replay_and_payload_mismatch():
    mgr = _mgr()
    state = await mgr.create_session()
    patch = {
        "operation": "SET",
        "path": "/active_need/budget/value",
        "value": 35000,
        "confidence": 0.95,
    }
    first = await mgr.apply_model_update(
        state.session_id,
        expected_revision=0,
        patch=patch,
        idempotency_key="same-key",
        client_message_id="m1",
        client_sequence=1,
    )
    assert first.revision == 1
    second = await mgr.apply_model_update(
        state.session_id,
        expected_revision=0,  # stale, but idempotency hits first in repo
        patch=patch,
        idempotency_key="same-key",
        client_message_id="m1",
        client_sequence=1,
    )
    assert second.status == CasStatus.IDEMPOTENT_REPLAY
    assert second.revision == 1
    current = await mgr.get_session(state.session_id)
    assert current.revision == 1

    with pytest.raises(ConversationDuplicateRequest):
        await mgr.apply_model_update(
            state.session_id,
            expected_revision=1,
            patch={**patch, "value": 99999},
            idempotency_key="same-key",
            client_message_id="m1",
            client_sequence=2,
        )


@pytest.mark.asyncio
async def test_ordering_rules():
    mgr = _mgr()
    state = await mgr.create_session()
    await mgr.apply_model_update(
        state.session_id,
        expected_revision=0,
        patch={
            "operation": "SET",
            "path": "/active_need/need_description",
            "value": "a",
            "confidence": 1.0,
        },
        idempotency_key="k1",
        client_message_id="m1",
        client_sequence=2,
    )
    with pytest.raises(ConversationOutOfOrder):
        await mgr.apply_model_update(
            state.session_id,
            expected_revision=1,
            patch={
                "operation": "SET",
                "path": "/active_need/need_description",
                "value": "b",
                "confidence": 1.0,
            },
            idempotency_key="k2",
            client_message_id="m2",
            client_sequence=1,
        )
    # Without sequence still works via revision
    ok = await mgr.apply_model_update(
        state.session_id,
        expected_revision=1,
        patch={
            "operation": "SET",
            "path": "/active_need/need_description",
            "value": "c",
            "confidence": 1.0,
        },
        idempotency_key="k3",
        client_message_id="m3",
    )
    assert ok.status == CasStatus.APPLIED
    assert ok.revision == 2


@pytest.mark.asyncio
async def test_patch_security_allowlist_and_old_value():
    mgr = _mgr()
    state = await mgr.create_session()
    with pytest.raises(ConversationPatchRejected):
        await mgr.apply_model_update(
            state.session_id,
            expected_revision=0,
            patch={
                "operation": "SET",
                "path": "/revision",
                "value": 99,
                "confidence": 1.0,
            },
            idempotency_key="bad1",
            client_message_id="m",
        )
    with pytest.raises(ConversationPatchRejected):
        await mgr.apply_model_update(
            state.session_id,
            expected_revision=0,
            patch={
                "operation": "SET",
                "path": "/active_need/budget/value",
                "value": 1,
                "old_value": 0,
                "confidence": 1.0,
            },
            idempotency_key="bad2",
            client_message_id="m",
        )
    with pytest.raises(ConversationPatchRejected):
        await mgr.apply_model_update(
            state.session_id,
            expected_revision=0,
            patch={
                "operation": "SET",
                "path": "/metadata/transcript",
                "value": ["hello"],
                "confidence": 1.0,
            },
            idempotency_key="bad3",
            client_message_id="m",
        )


@pytest.mark.asyncio
async def test_invalid_budget_not_persisted():
    mgr = _mgr()
    state = await mgr.create_session()
    await mgr.initialize_need(
        state.session_id,
        expected_revision=0,
        need=ActiveNeed(
            need_id=str(uuid4()),
            need_description="telefon",
            budget={
                "type": "RANGE",
                "value": 10000,
                "minimum": 1000,
                "maximum": 50000,
                "monthly_payment": None,
                "currency": "TRY",
            },
        ),
        idempotency_key="init",
        client_message_id="m0",
        client_sequence=1,
    )
    with pytest.raises(Exception):
        await mgr.apply_model_update(
            state.session_id,
            expected_revision=1,
            patch={
                "operation": "SET",
                "path": "/active_need/budget",
                "value": {
                    "type": "RANGE",
                    "value": 90000,
                    "minimum": 10000,
                    "maximum": 20000,
                    "monthly_payment": None,
                    "currency": "TRY",
                },
                "confidence": 0.99,
            },
            idempotency_key="bad-budget",
            client_message_id="m1",
            client_sequence=2,
        )
    current = await mgr.get_session(state.session_id)
    assert current.revision == 1
    assert current.active_need.budget["maximum"] == 50000


@pytest.mark.asyncio
async def test_reset_need_new_id_and_clears_clarification():
    mgr = _mgr()
    state = await mgr.create_session()
    await mgr.initialize_need(
        state.session_id,
        expected_revision=0,
        need=ActiveNeed(need_id="need-old", need_description="telefon"),
        idempotency_key="i0",
        client_message_id="m0",
        client_sequence=1,
    )
    await mgr.set_clarification(
        state.session_id,
        expected_revision=1,
        clarification=ClarificationState(
            required=True,
            reason_code="MISSING_PRODUCT_FORM",
            missing_concepts=("product_form",),
            question_intent="ASK_PRODUCT_FORM",
        ),
        idempotency_key="i1",
        client_message_id="m1",
        client_sequence=2,
    )
    result = await mgr.reset_need(
        state.session_id,
        expected_revision=2,
        idempotency_key="i2",
        client_message_id="m2",
        seed={"need_description": "bilgisayar", "budget": {"type": "UNKNOWN", "currency": "TRY"}},
        client_sequence=3,
    )
    assert result.status == CasStatus.APPLIED
    assert result.state is not None
    assert result.state.active_need is not None
    assert result.state.active_need.need_id != "need-old"
    assert result.state.active_need.need_description == "bilgisayar"
    assert result.state.clarification.required is False
    assert result.state.status == SessionStatus.ACTIVE


@pytest.mark.asyncio
async def test_ttl_idle_renew_absolute_cap():
    policy = ConversationStatePolicy(
        policy_code="CONVERSATION_DEFAULT",
        display_name="t",
        anonymous_idle_ttl_seconds=60,
        absolute_lifetime_seconds=120,
        idempotency_ttl_seconds=120,
    )
    mgr = ConversationStateManager(
        InMemoryConversationStateRepository(),
        policies=StaticPolicyProvider(policy),
    )
    state = await mgr.create_session(actor=Actor(type=ActorType.ANONYMOUS))
    absolute = state.absolute_expires_at
    touched = await mgr.touch_session(state.session_id)
    assert touched.expires_at <= absolute
    # Absolute never moves forward
    assert touched.absolute_expires_at == absolute


@pytest.mark.asyncio
async def test_expired_session_cannot_mutate():
    policy = ConversationStatePolicy(
        policy_code="CONVERSATION_DEFAULT",
        display_name="t",
        anonymous_idle_ttl_seconds=1,
        absolute_lifetime_seconds=1,
        idempotency_ttl_seconds=1,
    )
    repo = InMemoryConversationStateRepository()
    mgr = ConversationStateManager(repo, policies=StaticPolicyProvider(policy))
    state = await mgr.create_session()
    # Force expire
    stored = await repo.get(state.session_id)
    assert stored is not None
    stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    stored.absolute_expires_at = stored.expires_at
    await repo.delete(state.session_id)
    await repo.create(stored, idle_ttl_seconds=1)
    with pytest.raises(ConversationSessionExpired):
        await mgr.apply_model_update(
            state.session_id,
            expected_revision=0,
            patch={
                "operation": "SET",
                "path": "/active_need/need_description",
                "value": "x",
                "confidence": 1.0,
            },
            idempotency_key="e",
            client_message_id="m",
        )


@pytest.mark.asyncio
async def test_state_size_limit():
    policy = ConversationStatePolicy(
        policy_code="CONVERSATION_DEFAULT",
        display_name="t",
        max_state_size_bytes=200,
        max_string_length=50,
    )
    mgr = ConversationStateManager(
        InMemoryConversationStateRepository(),
        policies=StaticPolicyProvider(policy),
    )
    state = await mgr.create_session()
    with pytest.raises((ConversationStateTooLarge, ConversationPatchRejected, Exception)):
        await mgr.apply_model_update(
            state.session_id,
            expected_revision=0,
            patch={
                "operation": "SET",
                "path": "/active_need/need_description",
                "value": "x" * 5000,
                "confidence": 1.0,
            },
            idempotency_key="big",
            client_message_id="m",
        )


@pytest.mark.asyncio
async def test_orchestrator_bridge_conflict_requires_reevaluation():
    mgr = _mgr()
    bridge = DefaultOrchestratorBridge(mgr)
    state = await mgr.create_session()
    await mgr.apply_model_update(
        state.session_id,
        expected_revision=0,
        patch={
            "operation": "SET",
            "path": "/active_need/need_description",
            "value": "first",
            "confidence": 1.0,
        },
        idempotency_key="a",
        client_message_id="m1",
        client_sequence=1,
    )
    # Stale snapshot from revision 0
    conflict = await bridge.apply_router_result(
        state.session_id,
        snapshot=state,
        patch_or_need={
            "operation": "SET",
            "path": "/active_need/need_description",
            "value": "stale",
            "confidence": 1.0,
        },
        idempotency_key="b",
        client_message_id="m2",
        client_sequence=2,
    )
    assert hasattr(conflict, "require_reevaluation")
    assert conflict.require_reevaluation is True


def test_redis_key_hash_tags():
    from taksitlio.conversation_state.redis_repository import idempotency_key_digest

    sid = uuid4()
    raw = "abc-raw-external-key"
    sk = state_key(sid)
    ik = idem_key(sid, raw)
    assert f"{{{sid}}}" in sk
    assert f"{{{sid}}}" in ik
    assert raw not in ik
    assert idempotency_key_digest(raw) in ik


@pytest.mark.asyncio
async def test_reset_need_rejects_arbitrary_profile_fields():
    mgr = _mgr()
    state = await mgr.create_session()
    with pytest.raises(ConversationPatchRejected):
        await mgr.apply_model_update(
            state.session_id,
            expected_revision=0,
            patch={
                "operation": "RESET_NEED",
                "path": "/active_need",
                "confidence": 0.9,
                "need_profile": {
                    "need_description": "ok",
                    "need_id": "attacker-chosen",
                    "status": "COMPLETED",
                    "metadata": {"transcript": "secret"},
                },
            },
            idempotency_key="rn1",
            client_message_id="m-rn1",
        )


@pytest.mark.asyncio
async def test_get_missing_session():
    mgr = _mgr()
    with pytest.raises(ConversationSessionNotFound):
        await mgr.get_session(uuid4())

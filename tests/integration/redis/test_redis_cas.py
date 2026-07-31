"""Redis integration — skip=0 when REDIS_URL is required (ADR-009 §3)."""

from __future__ import annotations

import hashlib
import os
from uuid import uuid4

import pytest

redis = pytest.importorskip("redis")

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/15")
REQUIRE = os.environ.get("INTEGRATION_REQUIRE_REDIS", "1").lower() in {
    "1",
    "true",
    "yes",
}


@pytest.fixture
async def redis_client():
    import redis.asyncio as aioredis

    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001
        await client.aclose()
        if REQUIRE:
            pytest.fail(f"Redis required at {REDIS_URL}: {exc}")
        pytest.skip(f"Redis unavailable at {REDIS_URL}: {exc}")
    yield client
    async for key in client.scan_iter(match="taksitlio:chat:*"):
        await client.delete(key)
    await client.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_redis_health_and_lua_cas_load(redis_client):
    assert await redis_client.ping() is True
    from taksitlio.conversation_state.redis_repository import RedisConversationStateRepository

    repo = RedisConversationStateRepository(redis_client)
    assert repo._lua  # noqa: SLF001 — script body loaded
    assert "redis.call" in repo._lua


@pytest.mark.integration
@pytest.mark.asyncio
async def test_redis_session_create_update_conflict_replay(redis_client):
    from taksitlio.conversation_state.domain import CasStatus
    from taksitlio.conversation_state.errors import ConversationVersionConflict
    from taksitlio.conversation_state.manager import ConversationStateManager
    from taksitlio.conversation_state.redis_repository import (
        RedisConversationStateRepository,
        idem_key,
        idempotency_key_digest,
    )

    mgr = ConversationStateManager(RedisConversationStateRepository(redis_client))
    state = await mgr.create_session()
    raw_idem = f"raw-idem-{uuid4()}"
    patch = {
        "operation": "SET",
        "path": "/active_need/need_description",
        "value": "redis-ok",
        "confidence": 0.9,
    }
    first = await mgr.apply_model_update(
        state.session_id,
        expected_revision=0,
        patch=patch,
        idempotency_key=raw_idem,
        client_message_id="m1",
        client_sequence=1,
    )
    assert first.status == CasStatus.APPLIED

    digest = idempotency_key_digest(raw_idem)
    expected_key = idem_key(state.session_id, raw_idem)
    assert digest in expected_key
    assert raw_idem not in expected_key
    assert await redis_client.exists(expected_key) == 1

    replay = await mgr.apply_model_update(
        state.session_id,
        expected_revision=0,
        patch=patch,
        idempotency_key=raw_idem,
        client_message_id="m1",
        client_sequence=1,
    )
    assert replay.status == CasStatus.IDEMPOTENT_REPLAY

    with pytest.raises(ConversationVersionConflict):
        await mgr.apply_model_update(
            state.session_id,
            expected_revision=0,
            patch={
                "operation": "SET",
                "path": "/active_need/need_description",
                "value": "conflict",
                "confidence": 0.9,
            },
            idempotency_key=f"other-{uuid4()}",
            client_message_id="m2",
            client_sequence=2,
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_redis_same_key_different_payload_rejected(redis_client):
    from taksitlio.conversation_state.errors import ConversationDuplicateRequest
    from taksitlio.conversation_state.manager import ConversationStateManager
    from taksitlio.conversation_state.redis_repository import RedisConversationStateRepository

    mgr = ConversationStateManager(RedisConversationStateRepository(redis_client))
    state = await mgr.create_session()
    raw_idem = f"dup-{uuid4()}"
    await mgr.apply_model_update(
        state.session_id,
        expected_revision=0,
        patch={
            "operation": "SET",
            "path": "/active_need/need_description",
            "value": "first",
            "confidence": 0.9,
        },
        idempotency_key=raw_idem,
        client_message_id="m1",
        client_sequence=1,
    )
    with pytest.raises(ConversationDuplicateRequest):
        await mgr.apply_model_update(
            state.session_id,
            expected_revision=1,
            patch={
                "operation": "SET",
                "path": "/active_need/need_description",
                "value": "different-payload",
                "confidence": 0.9,
            },
            idempotency_key=raw_idem,
            client_message_id="m1",
            client_sequence=1,
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_redis_out_of_order_message(redis_client):
    from taksitlio.conversation_state.errors import ConversationOutOfOrder
    from taksitlio.conversation_state.manager import ConversationStateManager
    from taksitlio.conversation_state.redis_repository import RedisConversationStateRepository

    mgr = ConversationStateManager(RedisConversationStateRepository(redis_client))
    state = await mgr.create_session()
    await mgr.apply_model_update(
        state.session_id,
        expected_revision=0,
        patch={
            "operation": "SET",
            "path": "/active_need/need_description",
            "value": "seq-2",
            "confidence": 0.9,
        },
        idempotency_key=f"oo-{uuid4()}",
        client_message_id="m2",
        client_sequence=2,
    )
    with pytest.raises(ConversationOutOfOrder):
        await mgr.apply_model_update(
            state.session_id,
            expected_revision=1,
            patch={
                "operation": "SET",
                "path": "/active_need/need_description",
                "value": "seq-1",
                "confidence": 0.9,
            },
            idempotency_key=f"oo-{uuid4()}",
            client_message_id="m1",
            client_sequence=1,
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_redis_idle_and_absolute_ttl(redis_client):
    from taksitlio.conversation_state.manager import ConversationStateManager
    from taksitlio.conversation_state.policies import (
        ConversationStatePolicy,
        StaticPolicyProvider,
    )
    from taksitlio.conversation_state.redis_repository import (
        RedisConversationStateRepository,
        state_key,
    )

    policy = ConversationStatePolicy(
        policy_code="CONVERSATION_DEFAULT",
        display_name="ttl-test",
        anonymous_idle_ttl_seconds=2,
        authenticated_idle_ttl_seconds=2,
        absolute_lifetime_seconds=5,
    )
    mgr = ConversationStateManager(
        RedisConversationStateRepository(redis_client),
        policies=StaticPolicyProvider(policy),
    )
    state = await mgr.create_session()
    key = state_key(state.session_id)
    ttl = await redis_client.ttl(key)
    assert 0 < ttl <= 5


@pytest.mark.integration
@pytest.mark.asyncio
async def test_redis_raw_idempotency_key_absent_from_keyspace(redis_client):
    from taksitlio.conversation_state.manager import ConversationStateManager
    from taksitlio.conversation_state.redis_repository import (
        RedisConversationStateRepository,
        idempotency_key_digest,
    )

    mgr = ConversationStateManager(RedisConversationStateRepository(redis_client))
    state = await mgr.create_session()
    raw = f"mobile-secret-{uuid4()}"
    await mgr.apply_model_update(
        state.session_id,
        expected_revision=0,
        patch={
            "operation": "SET",
            "path": "/active_need/need_description",
            "value": "x",
            "confidence": 0.9,
        },
        idempotency_key=raw,
        client_message_id="m1",
        client_sequence=1,
    )
    digest = idempotency_key_digest(raw)
    async for key in redis_client.scan_iter(match="taksitlio:chat:*"):
        assert raw not in key
        if ":idem:" in key:
            assert digest in key


@pytest.mark.integration
@pytest.mark.asyncio
async def test_redis_unavailable_typed_error_no_in_memory_fallback():
    from datetime import datetime, timezone
    from uuid import uuid4 as _u

    from taksitlio.conversation_state.domain import ConversationState, SessionStatus
    from taksitlio.conversation_state.errors import ConversationRepositoryUnavailable
    from taksitlio.conversation_state.redis_repository import RedisConversationStateRepository
    from taksitlio.runtime_verification.probes import probe_redis

    class _Boom:
        def register_script(self, _lua):
            return self

        async def exists(self, *a, **k):
            raise ConnectionError("down")

        async def hset(self, *a, **k):
            raise ConnectionError("down")

        async def pexpire(self, *a, **k):
            raise ConnectionError("down")

    repo = RedisConversationStateRepository(_Boom())
    now = datetime.now(timezone.utc)
    state = ConversationState(
        session_id=_u(),
        status=SessionStatus.ACTIVE,
        created_at=now,
        updated_at=now,
        expires_at=now,
        absolute_expires_at=now,
    )
    with pytest.raises(ConversationRepositoryUnavailable):
        await repo.create(state, idle_ttl_seconds=30)

    bad = probe_redis(url="redis://invalid-redis-host.invalid:6379/15")
    assert bad.available is False
    assert bad.code is not None
    assert bad.code.value == "REDIS_UNAVAILABLE"


@pytest.mark.integration
def test_idempotency_digest_is_sha256():
    from taksitlio.conversation_state.redis_repository import idempotency_key_digest

    raw = "mobile-message-secret-key"
    digest = idempotency_key_digest(raw)
    assert digest == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert raw not in digest

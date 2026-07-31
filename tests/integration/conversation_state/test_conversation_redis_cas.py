"""Redis integration tests — require a live Redis (no skip).

CI / local:
  redis-server --port 6379 --daemonize yes   # or docker redis:7-alpine
  REDIS_URL=redis://127.0.0.1:6379/15 pytest -m integration tests/integration -q
"""

from __future__ import annotations

import hashlib
import os
from uuid import uuid4

import pytest

redis = pytest.importorskip("redis")

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/15")


@pytest.fixture
async def redis_client():
    import redis.asyncio as aioredis

    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001
        await client.aclose()
        pytest.fail(
            f"Redis required for integration tests but unavailable at {REDIS_URL}: {exc}"
        )
    yield client
    async for key in client.scan_iter(match="taksitlio:chat:*"):
        await client.delete(key)
    await client.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_redis_cas_and_idempotency(redis_client):
    from taksitlio.conversation_state.domain import CasStatus
    from taksitlio.conversation_state.errors import ConversationVersionConflict
    from taksitlio.conversation_state.manager import ConversationStateManager
    from taksitlio.conversation_state.redis_repository import (
        RedisConversationStateRepository,
        idem_key,
        idempotency_key_digest,
    )

    repo = RedisConversationStateRepository(redis_client)
    mgr = ConversationStateManager(repo)
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
    assert first.revision == 1

    # Raw external key must never appear in Redis keyspace
    digest = idempotency_key_digest(raw_idem)
    expected_key = idem_key(state.session_id, raw_idem)
    assert digest in expected_key
    assert raw_idem not in expected_key
    assert await redis_client.exists(expected_key) == 1
    async for key in redis_client.scan_iter(match="taksitlio:chat:*:idem:*"):
        assert raw_idem not in key

    replay = await mgr.apply_model_update(
        state.session_id,
        expected_revision=0,
        patch=patch,
        idempotency_key=raw_idem,
        client_message_id="m1",
        client_sequence=1,
    )
    assert replay.status == CasStatus.IDEMPOTENT_REPLAY
    assert replay.revision == 1

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

    loaded = await mgr.get_session(state.session_id)
    assert loaded.revision == 1
    assert loaded.active_need is not None
    assert loaded.active_need.need_description == "redis-ok"


@pytest.mark.integration
def test_idempotency_digest_is_sha256():
    from taksitlio.conversation_state.redis_repository import idempotency_key_digest

    raw = "mobile-message-secret-key"
    digest = idempotency_key_digest(raw)
    assert digest == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert raw not in digest

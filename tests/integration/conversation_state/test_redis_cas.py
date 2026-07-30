"""Redis integration tests — skipped when Redis is unavailable.

Run:
  REDIS_URL=redis://127.0.0.1:6379/15 pytest -m integration tests/integration -q
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

pytest.importorskip("redis")

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/15")


@pytest.fixture
async def redis_client():
    import redis.asyncio as redis

    client = redis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001
        await client.aclose()
        pytest.skip(f"Redis unavailable: {exc}")
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
    from taksitlio.conversation_state.redis_repository import RedisConversationStateRepository

    repo = RedisConversationStateRepository(redis_client)
    mgr = ConversationStateManager(repo)
    state = await mgr.create_session()

    idem = f"idem-{uuid4()}"
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
        idempotency_key=idem,
        client_message_id="m1",
        client_sequence=1,
    )
    assert first.status == CasStatus.APPLIED
    assert first.revision == 1

    replay = await mgr.apply_model_update(
        state.session_id,
        expected_revision=0,
        patch=patch,
        idempotency_key=idem,
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

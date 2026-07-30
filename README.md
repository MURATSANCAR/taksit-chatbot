# Taksitlio Chatbot

Fibabanka bağlı Taksitlio mobil chatbotu — production-grade MVP.

```text
Chat API → ConversationStateManager (Redis CAS + idempotency)
         → ModelRouter (FAST / FALLBACK / CLARIFY / SAFE_FAILURE)
         → apply_model_update(expected_revision=…)
         → Semantic category match → Kampanya → Ranking → Grounded cevap
```

Model adı / IP / port kodda yok. Router state yazmaz (ADR-003).

## ADR

* [`docs/adr/ADR-001-dynamic-model-routing.md`](docs/adr/ADR-001-dynamic-model-routing.md)
* [`docs/adr/ADR-002-model-deployment-runtime-separation.md`](docs/adr/ADR-002-model-deployment-runtime-separation.md)
* [`docs/adr/ADR-003-conversation-state-and-optimistic-locking.md`](docs/adr/ADR-003-conversation-state-and-optimistic-locking.md)
* [`docs/adr/ADR-004-dynamic-category-catalog-and-semantic-matching.md`](docs/adr/ADR-004-dynamic-category-catalog-and-semantic-matching.md)

## Conversation State Manager

| Bileşen | Konum |
|---------|--------|
| Domain + CAS sonuçları | `src/taksitlio/conversation_state/` |
| Lua compare-and-set | `src/taksitlio/conversation_state/lua/compare_and_set.lua` |
| Policy migration | `db/migrations/V006__conversation_state_policies.sql` |

Redis keys (Cluster hash-tag):

```text
taksitlio:chat:{sessionId}:state
taksitlio:chat:{sessionId}:idem:{idempotencyKey}
taksitlio:chat:{sessionId}:events
```

### Örnek: session + optimistic update

```python
from uuid import uuid4
from taksitlio.conversation_state import (
    ConversationStateManager,
    InMemoryConversationStateRepository,
)

mgr = ConversationStateManager(InMemoryConversationStateRepository())
state = await mgr.create_session()
result = await mgr.apply_model_update(
    state.session_id,
    expected_revision=state.revision,  # 0
    patch={
        "operation": "SET",
        "path": "/active_need/budget/value",
        "value": 50000,
        "confidence": 0.97,
        "evidence_text": "bütçeyi 50'ye çıkarabiliriz",
    },
    idempotency_key=str(uuid4()),
    client_message_id=str(uuid4()),
    client_sequence=1,
)
# result.status == APPLIED, result.revision == 1

# Stale revision → ConversationVersionConflict (state unchanged)
```

## Local tests

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

Conversation state unit tests:

```bash
pytest tests/unit/conversation_state -q
```

Redis integration (optional):

```bash
docker run --rm -p 6379:6379 redis:7-alpine
REDIS_URL=redis://127.0.0.1:6379/15 pytest -m integration tests/integration -q
```

## Docker / DB

```bash
cp .env.example .env
docker compose -f docker/docker-compose.yml up --build
# migrations auto; bootstrap models manually:
# psql "$DATABASE_URL" -f db/bootstrap/dev-models.sql
```

## Sıradaki katman

Dinamik kategori kataloğu + semantic category matcher (kategori kodu sabitlemeden).

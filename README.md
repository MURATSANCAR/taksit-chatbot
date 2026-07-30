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

## Dinamik kategori kataloğu + semantic matcher

Kategori kodları/isimleri kodda yer almaz. Katalog DB-driven, revision-versioned.
Detaylar için [ADR-004](docs/adr/ADR-004-dynamic-category-catalog-and-semantic-matching.md).

| Bileşen | Konum |
|---------|-------|
| Katalog servisi + publish validasyonu | `src/taksitlio/category_catalog/` |
| Semantic projeksiyon + async embedding worker | `src/taksitlio/category_embedding/` |
| Matcher (hybrid scorer + decision policy + cache) | `src/taksitlio/semantic_matching/` |
| CategoryResolutionApplier (bridge → ConversationStateManager) | `src/taksitlio/semantic_matching/state_bridge.py` |
| Migrations | `db/migrations/V007__dynamic_category_catalog.sql` `V008__semantic_match_policies.sql` `V009__category_embeddings_and_jobs.sql` |
| Admin ekran spec | `admin/specs/category-catalog-admin-screens.md` |

`V009` `CREATE EXTENSION IF NOT EXISTS vector` dener; `pgvector` yoksa
embedding kolonu `DOUBLE PRECISION[]` olarak taşınır ve uygulama katmanı
plain array yolunu kullanır (test/CI için).

### Boş katalog → publish → match akışı

```python
from taksitlio.category_catalog import (
    CategoryCatalogService,
    InMemoryCategoryCatalogRepository,
)
from taksitlio.category_embedding import (
    CategoryEmbeddingOutbox,
    CategoryEmbeddingWorker,
    InMemoryCategoryEmbeddingRepository,
)
from taksitlio.embeddings.client import LexicalEmbedder
from taksitlio.semantic_matching import (
    LexicalFallbackGateway,
    MatchQuery,
    SemanticCategoryMatcher,
    SemanticMatchPolicy,
    StaticSemanticMatchPolicyProvider,
)

catalog_repo = InMemoryCategoryCatalogRepository()
service = CategoryCatalogService(catalog_repo)
catalog = await service.create_catalog(catalog_code="DEMO", display_name="Demo")

category = await service.add_category(
    catalog_id=catalog.id,
    slug="mobile",
    semantic_description="Cep telefonu ve mobil cihaz talepleri",
)
await service.add_localization(
    category_id=category.id,
    locale=catalog.primary_locale,
    display_name="Mobil",
    synonyms=("telefon", "mobil"),
)
await service.add_alias(
    category_id=category.id,
    locale=catalog.primary_locale,
    alias_text="telefon",
)
await service.publish_revision(catalog.id)

embedding_repo = InMemoryCategoryEmbeddingRepository()
outbox = CategoryEmbeddingOutbox(embedding_repo)
snapshot = await service.get_published_snapshot(catalog.id)
await outbox.enqueue_for_snapshot(snapshot, embedding_profile_id="profile-1")

class _EmbClient:
    async def embed(self, texts): return await LexicalEmbedder(dim=64).embed(list(texts))

await CategoryEmbeddingWorker(embedding_repo, _EmbClient()).run_once()

matcher = SemanticCategoryMatcher(
    snapshot_provider=service,
    embedding_repository=embedding_repo,
    query_gateway=LexicalFallbackGateway(dim=64),
    policy_provider=StaticSemanticMatchPolicyProvider(SemanticMatchPolicy()),
)

result = await matcher.match(
    MatchQuery(
        text="kamera kalitesi iyi bir telefon arıyorum",
        catalog_id=catalog.id,
        locale=catalog.primary_locale,
        embedding_profile_id="profile-1",
        catalog_revision=snapshot.revision,
    )
)
# result.status == CategoryMatchStatus.MATCHED
# result.selected_category_id == category.id
```

Matcher **conversation state yazmaz**. Bridge `CategoryResolutionApplier`
manager üzerinden `apply_model_update` çağırır ve yalnızca
`category_resolution` alanına yazar.

### Category catalog + matcher testleri

```bash
pytest tests/unit/category_catalog tests/unit/category_embedding tests/unit/semantic_matching -q
```

Dinamik runtime kabul testi (in-memory, restart gerektirmez):

```bash
pytest tests/integration/semantic_matching -q
```

Redis integration (opsiyonel):

```bash
REDIS_URL=redis://127.0.0.1:6379/15 pytest -m integration tests/integration -q
```

## Sıradaki katman

Postgres repository implementasyonu + admin API + eval seti Türkçe genişletmesi.

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
* [`docs/adr/ADR-005-turkish-golden-set-and-semantic-evaluation.md`](docs/adr/ADR-005-turkish-golden-set-and-semantic-evaluation.md)
* [`docs/adr/ADR-006-semantic-matcher-quality-hardening.md`](docs/adr/ADR-006-semantic-matcher-quality-hardening.md)
* [`docs/adr/ADR-007-end-to-end-understanding-and-provisional-acceptance.md`](docs/adr/ADR-007-end-to-end-understanding-and-provisional-acceptance.md)

### End-to-end understanding + provisional gate (ADR-007)

The v3 sprint introduces four evaluation input lanes (see
[`src/taksitlio/evaluation/domain.py::EvaluationInputMode`](src/taksitlio/evaluation/domain.py)):

* `MATCHER_ORACLE_INPUT` — legacy oracle path, passes annotated
  `case.semantic_constraints` straight into the matcher.
* `END_TO_END_RUNTIME_INPUT` — utterance only → `DeterministicFastExtractor`
  (or a real remote FAST client) → `SemanticConstraintValidator` → matcher.
  Annotated constraints are **not** consulted.
* `FAST_EXTRACTION_ONLY` — runs FAST + validator and stops (no matcher).
* `MATCHER_ONLY` — alias of the oracle mode with empty constraints, useful
  for isolating the matcher's own contribution to failures.

Runtime wiring lives in
[`src/taksitlio/application/chat_orchestrator.py`](src/taksitlio/application/chat_orchestrator.py):
one turn = snapshot → FAST → validator → CAS (need summary) → matcher →
CAS (category resolution). Router / matcher repositories never write state
directly; the orchestrator retries at most once on
`ConversationVersionConflict` and raises typed
`OrchestratorRuntimeError("VERSION_CONFLICT_UNRECOVERABLE")` on a second
conflict.

Provisional gate CLI (ADR-007 §H):

```bash
# HUMAN_REVIEWED ≥ 100 + provisional thresholds + forbidden=0 unsafe=0
# → PROVISIONAL_ACCEPT (never full ACCEPT until v3 exits provisional).
python -m taksitlio.evaluation.cli run-category-eval \
  --dataset evaluation/datasets/validation/tr-category-validation.v3.jsonl \
  --fixture evaluation/fixtures/catalogs/category-fixture.v3.json \
  --gate-profile provisional

# Promote a balanced batch (25 MATCHED / 25 AMBIGUOUS / 25 NO_MATCH /
# 15 negation-or-correction / 10 typo-or-characterless) through the
# two-reviewer workflow. Reviewer identifiers must be opaque + distinct.
python -m taksitlio.evaluation.review_batch \
  --dataset evaluation/datasets/validation/tr-category-validation.v3.jsonl \
  --reviewer-a R-blind-a1 --reviewer-b R-blind-b1 --limit 100

# Diagnose decision-policy failures from a report JSON.
python -m taksitlio.evaluation.decision_audit \
  --report evaluation/reports/<run-id>.json --top 20
```

### Hardening CLI (ADR-006)

```bash
# Every rate metric now materialises as a ProportionMetric with Wilson CI.
python -m taksitlio.evaluation.cli run-category-eval \
  --dataset evaluation/datasets/validation/tr-category-validation.v2.jsonl \
  --fixture evaluation/fixtures/catalogs/category-fixture.v2.json \
  --gate-profile hardening

# How many HUMAN_REVIEWED cases are still needed to promote a dataset.
python -m taksitlio.evaluation.cli review-status \
  --dataset evaluation/datasets/validation/tr-category-validation.v2.jsonl

# Verify that the embedding challenger never silently falls back to lexical.
python -m taksitlio.evaluation.cli compare-embeddings --gateway lexical      # exits 2
python -m taksitlio.evaluation.cli compare-embeddings --gateway unavailable  # exits 2
```

`--gate-profile hardening` picks up the tighter targets from
`evaluation/config/evaluation_defaults.json::hardening_quality_gate_thresholds`.
DRAFT / synthetic bootstrap datasets can only ever reach
`PROVISIONAL_ACCEPT` / `REJECT` / `INSUFFICIENT_REVIEWED_DATA` — no matter
how good the metrics look, promotion always requires enough
HUMAN_REVIEWED cases.

## Conversation State Manager

| Bileşen | Konum |
|---------|--------|
| Domain + CAS sonuçları | `src/taksitlio/conversation_state/` |
| Lua compare-and-set | `src/taksitlio/conversation_state/lua/compare_and_set.lua` |
| Policy migration | `db/migrations/V006__conversation_state_policies.sql` |

Redis keys (Cluster hash-tag):

```text
taksitlio:chat:{sessionId}:state
taksitlio:chat:{sessionId}:idem:{sha256(idempotency_key)}
taksitlio:chat:{sessionId}:events
```

Raw idempotency key Redis key / log / metric label içinde bulunmaz.

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

Redis integration (**required** — skip yok; CI Redis service kullanır):

```bash
docker run --rm -d --name taksitlio-test-redis -p 6379:6379 redis:7-alpine
REDIS_URL=redis://127.0.0.1:6379/15 pytest -m integration tests/integration -q
```

Dynamic category acceptance (restart yok):

```bash
pytest tests/integration/semantic_matching -q
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

## Category-match evaluation (ADR-005)

Türkçe kategori eşleme kalitesi kampanya katmanına geçmeden önce
ölçülür. Değerlendirme paketi fixture key temelli izole bir katalog
oluşturur (iki aşamalı publish — `prepare_embed_and_publish`), matcher
üzerinde çalıştırır ve config'ten okunan eşiklere göre ACCEPT / REJECT
kalite kapısı raporu üretir. **Kalite kapısı geçmeden kampanya
geliştirmesine geçiş yoktur.**

Detay dokümanlar:

* [ADR-005](docs/adr/ADR-005-turkish-golden-set-and-semantic-evaluation.md)
* [`admin/specs/category-evaluation-admin-screens.md`](admin/specs/category-evaluation-admin-screens.md)
* [`evaluation/datasets/README.md`](evaluation/datasets/README.md)

Paket ve dosya düzeni:

| Bileşen | Konum |
|---------|-------|
| Domain + runner + evaluator + CLI | `src/taksitlio/evaluation/` |
| Fixture katalog | `evaluation/fixtures/catalogs/category-fixture.v1.json` |
| Dataset splitleri | `evaluation/datasets/{development,golden}/` |
| Şemalar | `evaluation/schemas/` |
| Config + baseline | `evaluation/config/`, `evaluation/baselines/` |
| Raporlar (gitignored) | `evaluation/reports/`, `evaluation/private/` |

CLI komutları:

```bash
python -m taksitlio.evaluation.cli validate-dataset \
    --dataset evaluation/datasets/development/tr-category-dev.v1.jsonl \
    --check-split-integrity

python -m taksitlio.evaluation.cli run-category-eval \
    --dataset evaluation/datasets/golden/tr-category-validation.v1.jsonl \
    --mode FULL --workers 4

python -m taksitlio.evaluation.cli benchmark-category-match \
    --dataset evaluation/datasets/development/tr-category-dev.v1.jsonl \
    --workers 8

python -m taksitlio.evaluation.cli compare-runs \
    --baseline evaluation/reports/<older>.json \
    --candidate evaluation/reports/<newer>.json

python -m taksitlio.evaluation.cli tune-policy \
    --dataset evaluation/datasets/golden/tr-category-validation.v1.jsonl \
    --grid-steps 4    # HOLDOUT üzerinde çalıştırmaz — reddeder (ADR-005 §6)
```

Kalite kapısı:

* Config: [`evaluation/config/evaluation_defaults.json`](evaluation/config/evaluation_defaults.json)
* Bootstrap baseline: [`evaluation/baselines/category-match-baseline.v1.json`](evaluation/baselines/category-match-baseline.v1.json)
* Standart raporlar **ham utterance içermez** (`privacy.py`). Debug modu
  (`--debug-utterances`) yalnızca `evaluation/private/` altına yazar ve
  bu klasör `.gitignore` ile hariç tutulur.
* Evaluation **hiçbir zaman** policy'yi otomatik ACTIVE yapmaz;
  challenger olarak kaydedilir, AuditService + admin onayı gerekir.

## Sıradaki katman

Postgres repository implementasyonu + admin API + HUMAN_REVIEWED (≥ 2 reviewer) golden set büyütmesi.

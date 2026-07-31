# Runbook — ADR-009 P1 Canlı Runtime Doğrulama

Status: **operasyonel** (2026-07-31)  
Kod: matcher / ranking / threshold / dataset **değiştirilmez**.

## Başlangıç kapıları (kod tarafı sonrası)

| Gate | Durum |
|---|---|
| Quality | QUALITY_READY (test-double baseline) |
| Runtime | BLOCKED_DEPENDENCY |
| Provisional | BLOCKED_DEPENDENCY |
| Campaign | CLOSED |
| Unit tests | 224 passed (`--ignore=tests/integration`) |

Blocker’lar canlı sunucuda kapanmalıdır:

```text
REDIS_UNAVAILABLE
POSTGRES_UNAVAILABLE
PGVECTOR_EXTENSION_UNAVAILABLE
FAST_DEPLOYMENT_UNAVAILABLE
EMBEDDING_DEPLOYMENT_UNAVAILABLE
```

---

## 0. Önkoşullar

```bash
cd /path/to/Fibabank_Chatbot
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Değiştirilmeyecekler:

* Matcher heuristics / ranking ağırlıkları
* Kalite threshold’ları
* Evaluation expected sonuçları / fixture annotation’ları
* Campaign kodu

---

## 1. Hedef sunucuyu kontrol et

```bash
uname -a
lscpu
free -h
df -h
docker --version
docker compose version
```

Beklenen: Docker + Compose hazır, yeterli disk, 6379/5432 boş veya bilerek map’li, FAST/embedding model dosyaları erişilebilir.

Docker yoksa önce Docker Engine + Compose kurun; bu runbook’a devam etmeyin.

---

## 2. Environment dosyasını hazırla

```bash
cp .env.example .env.runtime
chmod 600 .env.runtime
```

`.env.runtime` içine gerçek değerleri girin (örnek anahtarlar `.env.example` içinde). Kurallar:

* Model adı kaynak koda yazılmaz
* Endpoint migration’a yazılmaz
* Parolalar commit edilmez (`.env.runtime` gitignore’da)
* `EMBEDDING_DIM` = modelin gerçek çıktı boyutu

Shell’e yükle:

```bash
set -a && source .env.runtime && set +a
export PGVECTOR_URL="${PGVECTOR_URL:-$DATABASE_URL}"
export INTEGRATION_REQUIRE_REDIS=1
export INTEGRATION_REQUIRE_PG=1
```

Host’tan test ederken `DATABASE_URL` / `REDIS_URL` içindeki `postgres` / `redis` hostname’lerini `127.0.0.1` ile değiştirin (compose port publish).

---

## 3. Redis ve pgvector servislerini başlat

```bash
docker compose \
  --env-file .env.runtime \
  -f docker/docker-compose.runtime.yml \
  --profile runtime-verification \
  up -d redis postgres

docker compose \
  --env-file .env.runtime \
  -f docker/docker-compose.runtime.yml \
  --profile runtime-verification \
  ps

docker compose \
  --env-file .env.runtime \
  -f docker/docker-compose.runtime.yml \
  --profile runtime-verification \
  logs --tail=100 redis postgres
```

Redis:

```bash
docker compose -f docker/docker-compose.runtime.yml --profile runtime-verification exec redis redis-cli ping
# → PONG
```

PostgreSQL + pgvector:

```bash
docker compose -f docker/docker-compose.runtime.yml --profile runtime-verification exec postgres \
  psql -U taksitlio -d taksitlio -c "SELECT version();"

docker compose -f docker/docker-compose.runtime.yml --profile runtime-verification exec postgres \
  psql -U taksitlio -d taksitlio -c "
    CREATE EXTENSION IF NOT EXISTS vector;
    SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';"
```

Not: `docker/docker-compose.runtime.yml` ilk boot’ta `db/migrations` klasörünü initdb’ye mount eder. Temiz volume’da V001…V014 otomatik uygulanır.

---

## 4. Migration’ları uygula / doğrula

Alembic yok. Resmi komut:

```bash
# Host DATABASE_URL → 127.0.0.1:5432 (publish edilmiş port)
python -m taksitlio.db.migrate
python -m taksitlio.db.migrate   # ikinci kez — hata vermemeli
```

Tablo listesi:

```bash
psql "$DATABASE_URL" -c "
SELECT table_name
FROM information_schema.tables
WHERE table_schema='public'
ORDER BY table_name;"
```

---

## 5. Redis integration

```bash
REDIS_URL="${REDIS_URL_HOST:-redis://127.0.0.1:6379/15}" \
pytest -m integration tests/integration/redis tests/integration/conversation_state -q
```

Kabul: `passed > 0`, `failed = 0`, `skipped = 0`.

---

## 6. pgvector integration

```bash
PGVECTOR_URL="$DATABASE_URL" INTEGRATION_REQUIRE_PG=1 \
pytest -m integration tests/integration/pgvector tests/integration/category_embedding -q
```

Kabul: `passed > 0`, `failed = 0`, `skipped = 0`.

---

## 7. FAST runtime’ı başlat

OpenAI-compatible sözleşme:

* `GET /health`
* `POST /v1/chat/completions`
* context 4096, max_tokens 128, temperature 0
* thinking off, streaming off, JSON schema required
* parallel slots başlangıç 4

```bash
curl -sS "$FAST_PROVIDER_BASE_URL/health"

curl -sS "$FAST_PROVIDER_BASE_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"$FAST_MODEL_REFERENCE\",
    \"messages\": [{\"role\":\"user\",\"content\":\"Telefon istemiyorum, tablet bakıyorum.\"}],
    \"temperature\": 0,
    \"max_tokens\": 128,
    \"stream\": false,
    \"response_format\": {\"type\": \"json_object\"}
  }"
```

Beklenen: geçerli NeedProfile JSON; positive≈tablet, negative≈telefon; kategori ID / fixture key / thinking metni yok.

---

## 8. FAST bootstrap

`envsubst` ile (psql `-v` değil — SQL `${POC_FAST_*}` kullanır):

```bash
export POC_FAST_PROVIDER_TYPE="${POC_FAST_PROVIDER_TYPE:-OPENAI_COMPAT}"
export POC_FAST_BASE_URL="$FAST_PROVIDER_BASE_URL"
export POC_FAST_MODEL_REFERENCE="$FAST_MODEL_REFERENCE"
export POC_FAST_RUNTIME_ALIAS="${FAST_RUNTIME_ALIAS:-poc-fast-understanding}"
export POC_FAST_CONTEXT_LIMIT="${FAST_CONTEXT_LIMIT:-4096}"
export POC_FAST_MAX_OUTPUT_TOKENS="${FAST_MAX_OUTPUT_TOKENS:-128}"
export POC_FAST_TIMEOUT_MS="${FAST_TIMEOUT_MS:-3000}"
export POC_FAST_PARALLEL_SLOTS="${FAST_PARALLEL_SLOTS:-4}"
export POC_FAST_TEMPERATURE=0.000
export POC_FAST_QUANTIZATION="${FAST_QUANTIZATION:-unspecified}"
export POC_FAST_CREDENTIAL_REF="${FAST_CREDENTIAL_REF:-secret://poc/fast-token}"

envsubst < db/bootstrap/poc-fast-understanding.sql | psql "$DATABASE_URL"
```

Kontrol:

```sql
SELECT p.profile_code, d.deployment_code, d.runtime_alias, d.status, c.base_url
FROM ai_model_profiles p
JOIN ai_model_deployments d ON d.model_profile_id = p.id
JOIN ai_provider_connections c ON c.id = d.provider_connection_id
WHERE p.task_type = 'UNDERSTANDING';

SELECT * FROM ai_route_versions
WHERE task_code = 'NEED_UNDERSTANDING' AND is_active = TRUE;
```

---

## 9–10. Embedding runtime + bootstrap

```bash
curl -sS "$EMBEDDING_PROVIDER_BASE_URL/health"

curl -sS "$EMBEDDING_PROVIDER_BASE_URL/v1/embeddings" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"$EMBEDDING_MODEL_REFERENCE\",\"input\":[\"test\"]}" \
  | jq '.data[0].embedding | length'
# → EMBEDDING_DIM ile aynı
```

```bash
export POC_EMBEDDING_PROVIDER_TYPE="${POC_EMBEDDING_PROVIDER_TYPE:-OPENAI_COMPAT}"
export POC_EMBEDDING_BASE_URL="$EMBEDDING_PROVIDER_BASE_URL"
export POC_EMBEDDING_MODEL_REFERENCE="$EMBEDDING_MODEL_REFERENCE"
export POC_EMBEDDING_DIM="$EMBEDDING_DIM"
export POC_EMBEDDING_RUNTIME_ALIAS="${EMBEDDING_RUNTIME_ALIAS:-poc-category-embedding}"
export POC_EMBEDDING_TIMEOUT_MS="${EMBEDDING_TIMEOUT_MS:-2000}"
export POC_EMBEDDING_PARALLEL_SLOTS="${EMBEDDING_PARALLEL_SLOTS:-4}"
export POC_EMBEDDING_QUANTIZATION="${EMBEDDING_QUANTIZATION:-unspecified}"
export POC_EMBEDDING_SPACE_ID="${EMBEDDING_SPACE_ID:-default}"
export POC_EMBEDDING_CREDENTIAL_REF="${EMBEDDING_CREDENTIAL_REF:-secret://poc/embedder-token}"

envsubst < db/bootstrap/poc-category-embedding.sql | psql "$DATABASE_URL"
```

```sql
SELECT p.profile_code, d.deployment_code, d.runtime_alias, d.status, c.base_url
FROM ai_model_profiles p
JOIN ai_model_deployments d ON d.model_profile_id = p.id
JOIN ai_provider_connections c ON c.id = d.provider_connection_id
WHERE p.task_type = 'EMBEDDING';
```

---

## 11. Fixture katalog embeddinglerini yeniden üret

Lexical / test-double vectorleri **yeniden kullanmayın**. Yeni revision + gerçek embedding:

```bash
# Mevcut worker CLI repo’da genişliyorsa onu kullanın; yoksa fixture catalog
# publish + embedding worker akışını ADR-004/006 dokümanına göre çalıştırın.
python -m taksitlio.category_embedding.worker --help || true
```

Kontrol:

```sql
SELECT status, COUNT(*) FROM catalog_category_embeddings GROUP BY status;
SELECT DISTINCT embedding_dimension
FROM catalog_category_embeddings WHERE status = 'READY';
```

Beklenen: `FAILED=0`, `PENDING=0`, tek `embedding_dimension`.

---

## 12. Tam integration suite

```bash
REDIS_URL="${REDIS_URL_HOST:-redis://127.0.0.1:6379/15}" \
PGVECTOR_URL="$DATABASE_URL" \
DATABASE_URL="$DATABASE_URL" \
INTEGRATION_REQUIRE_REDIS=1 INTEGRATION_REQUIRE_PG=1 \
FAST_PROVIDER_BASE_URL="$FAST_PROVIDER_BASE_URL" \
FAST_MODEL_REFERENCE="$FAST_MODEL_REFERENCE" \
EMBEDDING_PROVIDER_BASE_URL="$EMBEDDING_PROVIDER_BASE_URL" \
EMBEDDING_MODEL_REFERENCE="$EMBEDDING_MODEL_REFERENCE" \
EMBEDDING_DIM="$EMBEDDING_DIM" \
pytest -m integration tests/integration -q
```

Kabul: `failed=0`, `skipped=0`.

---

## 13. ADR-009 runtime runner

```bash
python evaluation/_run_adr008_p1.py
```

Gerçek yollar kullanılmalı: Redis CAS, `RemoteFastExtractor`, `StrictOpenAICompatibleEmbedder`, pgvector.  
Yasak: in-memory repo, stub FAST, LexicalEmbedder, sessiz fallback.

---

## 14. Raporlar

```text
evaluation/reports/adr008-p1-redis-integration.json
evaluation/reports/adr008-p1-pgvector-integration.json
evaluation/reports/adr008-p1-fast-quality.json
evaluation/reports/adr008-p1-fast-latency.json
evaluation/reports/adr008-p1-embedding-quality.json
evaluation/reports/adr008-p1-pgvector-benchmark.json
evaluation/reports/adr008-p1-e2e-runtime.json
evaluation/reports/adr008-p1-gate.json
```

Gate dosyasında `real_*_measured = true` ve boş blocker listesi beklenir.

---

## 15–16. Kalite + performans kapıları

Oracle: top_1≥0.65, top_2≥0.90, required≥0.88, forbidden=0, unsafe=0  
E2E: status≥0.78, top_1≥0.65, top_2≥0.90, required≥0.88, forbidden=0, unsafe=0  
FAST: invalid_schema=0, forbidden_id=0, neg_recall≥0.95, corr_recall≥0.90  

FAST warm P50 < 2000ms, P95 < 3000ms; pgvector retrieval P95 < 100ms; matcher P95 < 400ms;  
E2E warm P50 < 3000ms, P95 < 4000ms. Kaçırılan hedef gizlenmez — stage latency raporlanır.

---

## 17. Gate kararı

Hepsi yeşil:

```text
Quality Gate:      QUALITY_READY
Runtime Gate:      RUNTIME_READY
Provisional Gate:  PROVISIONAL_ACCEPT
Campaign Gate:     READY_TO_OPEN
```

Dependency eksik → `BLOCKED_DEPENDENCY` + Campaign `CLOSED`  
Kalite kaçarsa → `RUNTIME_QUALITY_REJECT` + Campaign `CLOSED`

Final ACCEPT bu runbook kapsamında **verilmez**.

---

## 18. Görev sonu raporu şablonu

Operatör şunları doldurur:

* Unit / integration passed·skipped·failed
* Redis / pgvector sonuçları
* FAST & embedding profile/deployment ref, latency, throughput
* pgvector 100/1k/10k P50/P95/P99 + index + plan
* Oracle / E2E metrikleri, forbidden/unsafe
* E2E stage latency
* Dört gate sonucu + kalan blocker’lar

Sonraki aşama (yalnızca `PROVISIONAL_ACCEPT` sonrası): kampanya domain modeli, ingestion ve retrieval tasarımı.

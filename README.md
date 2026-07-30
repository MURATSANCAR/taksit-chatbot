# Taksitlio Chatbot

Fibabanka bağlı Taksitlio mobil chatbotu — üretim seviyesinde MVP.

```text
Chat API → Conversation State (Redis) → ModelRouter (FAST / FALLBACK)
         → Semantic category match → Kampanya retrieval
         → Deterministik uygunluk → Dinamik ranking
         → Grounded cevap + üyelik CTA
```

Model adları, kategori listeleri, kampanyalar, promptlar ve confidence eşikleri **kodda sabitlenmez**; PostgreSQL + yönetim API’sinden yönetilir.

## Mimari

* [`docs/architecture/MVP-ARCHITECTURE.md`](docs/architecture/MVP-ARCHITECTURE.md)
* [`docs/adr/ADR-001-dynamic-model-routing.md`](docs/adr/ADR-001-dynamic-model-routing.md)

## Bileşenler

| Katman | Konum |
|--------|--------|
| Chat + Admin API | `src/taksitlio/api/` |
| Pipeline orchestrator | `src/taksitlio/pipeline/` |
| ModelGateway / ModelRouter | `src/taksitlio/model_gateway/`, `model_router/` |
| Redis Conversation State | `src/taksitlio/conversation/` |
| Semantic category matcher | `src/taksitlio/category/` |
| Kampanya / uygunluk / ranking | `src/taksitlio/campaign/` |
| Grounded response | `src/taksitlio/response/` |
| DB migrations | `db/migrations/V001`–`V004` |
| Türkçe golden set | `eval/golden/` |
| Admin ekran spesifikasyonları | `admin/specs/ai-admin-screens.md` |

## Hızlı başlangıç (in-memory demo)

LLM sunucusu olmadan kategori → kampanya → grounded template akışını test etmek için:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
export ALLOW_IN_MEMORY=true
pytest
```

API (in-memory; anlama katmanı gerçek llama.cpp ister):

```bash
export ALLOW_IN_MEMORY=true
uvicorn taksitlio.main:app --reload --port 8000
```

## Docker (Postgres + Redis + API)

```bash
cp .env.example .env
docker compose -f docker/docker-compose.yml up --build
```

Migration’lar Postgres ilk açılışta `/docker-entrypoint-initdb.d` üzerinden uygulanır. Sonra `ai_model_profiles.endpoint_url` alanlarını gerçek llama.cpp sunucularına admin API veya SQL ile bağlayın.

## API

* `GET /health`
* `POST /v1/chat` — `{ "session_id", "message", "user_id?" }`
* `DELETE /v1/sessions/{session_id}`
* `GET /v1/admin/models`
* `PATCH /v1/admin/models/{profile_code}`
* `POST /v1/admin/models/compare`
* `GET /v1/admin/prompts/{prompt_code}`
* `POST /v1/admin/prompts/{prompt_code}/activate`

## POC FAST adayları

* Aday A: `Qwen3.5-4B` Q4_K_M → `FAST_UNDERSTANDING`
* Aday B: `Qwen3-4B-Instruct-2507` Q4_K_M → `FAST_UNDERSTANDING_CHALLENGER`
* Fallback: `DEEP_UNDERSTANDING`
* Cevap: `RESPONSE_GENERATION`
* Embedding: `EMBEDDING_DEFAULT`

Kesin FAST seçimi `eval/golden` + eşzamanlı benchmark sonrası verilir.

## Ortam değişkenleri

Yalnızca altyapı — model/kategori içeriği değil:

* `DATABASE_URL`
* `REDIS_URL`
* `REDIS_KEY_PREFIX`
* `SESSION_TTL_SECONDS`
* `ALLOW_IN_MEMORY` (lokal demo)

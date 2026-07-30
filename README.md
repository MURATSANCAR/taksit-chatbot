# Taksitlio Chatbot

Fibabanka bağlı Taksitlio mobil chatbotu — production-grade MVP routing çekirdeği.

```text
Chat API → Conversation State → ModelRouter (FAST / FALLBACK / CLARIFY / SAFE_FAILURE)
         → system_confidence + reason codes
         → Semantic category match → Kampanya → Ranking → Grounded cevap
```

Model adı, IP ve port **uygulama kodunda yoktur**. Profile ≠ connection ≠ deployment (ADR-002).

## Mimari / ADR

* [`docs/architecture/MVP-ARCHITECTURE.md`](docs/architecture/MVP-ARCHITECTURE.md)
* [`docs/adr/ADR-001-dynamic-model-routing.md`](docs/adr/ADR-001-dynamic-model-routing.md)
* [`docs/adr/ADR-002-model-deployment-runtime-separation.md`](docs/adr/ADR-002-model-deployment-runtime-separation.md)

## Routing çekirdeği

| Bileşen | Konum |
|---------|--------|
| ModelGateway (deployment-resolved) | `src/taksitlio/model_gateway/` |
| ModelRouter + reason codes | `src/taksitlio/model_router/router.py` |
| SystemConfidenceEvaluator | `src/taksitlio/model_router/confidence.py` |
| RuntimeHealthRegistry (in-memory) | `src/taksitlio/model_router/health.py` |
| Absolute Deadline | `src/taksitlio/model_router/deadline.py` |
| Route version selector | `src/taksitlio/model_router/route_selector.py` |
| Typed conversation patch | `src/taksitlio/conversation/patch.py` |
| AuditService | `src/taksitlio/audit/` |

## DB

| Dosya | İçerik |
|-------|--------|
| `db/migrations/V001__ai_model_management.sql` | Şema only (profiles, connections, deployments, route_versions, audit, logs) |
| `db/migrations/V002__ai_default_policies.sql` | Teknik default politikalar (host yok) |
| `db/migrations/V003+` | Kategori / kampanya / prompt |
| `db/bootstrap/dev-models.sql` | Dev bağlantıları (docker DNS) |
| `db/bootstrap/poc-models.sql` | POC A/B route’ları |

`endpoint_url` on `ai_model_profiles` is **DEPRECATED** — gateway ignores it.

## Local test

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

Routing-core suite:

```bash
pytest tests/test_routing_core.py -q
```

In-memory API (no LLM):

```bash
export ALLOW_IN_MEMORY=true
uvicorn taksitlio.main:app --reload --port 8000
```

## Docker

```bash
cp .env.example .env
docker compose -f docker/docker-compose.yml up --build
# after migrations, apply bootstrap explicitly:
# psql "$DATABASE_URL" -f db/bootstrap/dev-models.sql
```

## Kabul özeti (bu aşama)

* Routing model self-confidence’a tek başına bağlı değil (`system_confidence`)
* Eksik bilgi → `CLARIFY`; invalid/comprehension → `FALLBACK`
* Profile / connection / deployment ayrıldı
* Absolute deadline; süre yetmezse fallback yok → `SAFE_FAILURE`
* Uygulama kodunda vendor model adı / loopback IP yok

## Sıradaki adım

Conversation State Manager + Redis optimistic locking.

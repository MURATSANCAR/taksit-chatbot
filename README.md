# Taksitlio Chatbot

Fibabanka bağlı Taksitlio mobil chatbotu için MVP iskeleti.

Odak: **Gerçek Zamanlı Türkçe Anlama Katmanı** — FAST + FALLBACK local LLM, dinamik model profilleri, yönetim panelinden değiştirilebilir routing.

## Mimari

Tam belge: [`docs/architecture/MVP-ARCHITECTURE.md`](docs/architecture/MVP-ARCHITECTURE.md)

```text
Chat API → Conversation State → ModelRouter → FAST / FALLBACK
         → Semantic category match → Kampanya → Ranking → Grounded cevap
```

Model adları kod veya `.env` içine gömülmez. Profiller `ai_model_profiles`, görev yönlendirmesi `ai_task_routes` tablolarındadır.

## Bu sürümde bulunanlar

| Bileşen | Konum |
|---------|--------|
| MVP mimari + Türkçe anlama katmanı | `docs/architecture/` |
| ADR: dinamik model routing | `docs/adr/ADR-001-dynamic-model-routing.md` |
| DB şeması + seed | `db/migrations/V001__ai_model_management.sql` |
| Need / update JSON Schema | `src/taksitlio/schemas/` |
| `ModelGateway` | `src/taksitlio/model_gateway/` |
| `ModelRouter` | `src/taksitlio/model_router/` |
| Session UPDATE uygulayıcı | `src/taksitlio/conversation/state.py` |
| Admin AI ekran spesifikasyonları | `admin/specs/ai-admin-screens.md` |

## POC FAST adayları

* Aday A: `Qwen3.5-4B` Q4_K_M → profil `FAST_UNDERSTANDING`
* Aday B: `Qwen3-4B-Instruct-2507` Q4_K_M → profil `FAST_UNDERSTANDING_CHALLENGER`
* Fallback: mevcut büyük local model → `DEEP_UNDERSTANDING`

Kesin seçim Türkçe golden set + eşzamanlı benchmark sonrası verilir.

## Geliştirme

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Sonraki adımlar

1. PostgreSQL migration’ı uygula; endpoint URL’lerini gerçek llama.cpp sunucularına bağla
2. Redis Conversation State Manager
3. Semantic category matcher + kampanya motoru
4. 1.000+ cümlelik Türkçe değerlendirme seti ve benchmark

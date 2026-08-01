# Taksitlio E2E System Map

Verification map for TASK-E2E-PROD-VERIFY. Describes the **existing** production path;
no parallel catalog or second source-of-truth.

## Stack (discovery)

| Area | Implementation |
|---|---|
| Backend | FastAPI (`src/taksitlio/api`) |
| Frontend | Guest UI `web/taksitlio` (vanilla JS modules) |
| Database | PostgreSQL (`DATABASE_URL` / nanobase) |
| Migrations | Flyway-style `db/migrations/VNNN__*.sql` via `python -m taksitlio.db.migrate` |
| Redis | Conversation CAS / idempotency (ADR-003) |
| Queue | Scheduler jobs (`V020`) + LLM job SKIP LOCKED claim |
| LLM | `llm_routing` + `LlmUnderstandingWorker` / remote OpenAI-compatible |
| Tests | pytest unit/acceptance/integration + Query Golden CLI |

---

## End-to-end flow

```text
POST /v1/chat  OR  POST /v1/search-sessions
  → ChatPipeline / SearchOrchestrator
  → fast_parse + entity_resolution
  → detect_gaps + clarification policy
  → (optional) async LLM job
  → product pool / product_search_projection retrieval
  → finance options / ranking
  → claim validation / grounded response
  → SSE progress events → guest UI
```

---

## Step detail

### 1. User query ingress

| | |
|---|---|
| **Endpoint** | `POST /v1/chat` (`src/taksitlio/api/routes/chat.py::chat`) |
| **Alt** | `POST /v1/search-sessions` (`api/routes/search_sessions.py`) |
| **Input** | `session_id`, `message`, optional `product_phase` |
| **Output** | `ChatMessageOut` (reply, cards, clarification, diagnostics, `events_url`) |
| **Deps** | `container.pipeline`, optional `llm_understanding_worker` |
| **Errors** | HTTP 502 on pipeline exception |

### 2. Search session creation

| | |
|---|---|
| **Where** | `SearchOrchestrator.start` (`search_sessions/orchestrator.py`) via `bridge_search_start` (`search_sessions/chat_bridge.py`) |
| **Persist** | In-memory repo and/or `PostgresSearchSessionRepository` (`search_sessions/postgres.py`) |
| **Input** | `conversation_id`, `message`, `user_id` |
| **Output** | session id, `query_version`, status, events |
| **Versioning** | `active_query_version` increments on new user message / clarification answer |
| **Errors** | session missing → hydrate or 404 |

### 3. Fast parser

| | |
|---|---|
| **Fn** | `fast_parse` (`query_understanding/fast_parser.py`) |
| **Input** | utterance + `CatalogHints` (merchants/brands/categories/institutions) |
| **Output** | `FastParseResult` (intent, entities, budget, terms, `field_confidence`, `route`) |
| **Deps** | `entity_resolution.resolve_entity`, Turkish normalize |
| **Errors** | unresolved spans; low confidence → clarification/LLM route |

### 4. Entity resolver

| | |
|---|---|
| **Fn** | `resolve_entity` / `score_candidate` (`entity_resolution/__init__.py`) |
| **Catalog** | Prefer `entity_search_index` via `catalog_hints_from_entity_index` (`catalog_projection/hints.py`); else in-memory CatalogHints |
| **Mechanism** | exact → alias → normalized → token-set → trigram → edit → n-gram → confidence/gap |
| **Policy** | `entity_resolution_policies` (auto ≥0.92, clarify ≥0.78) |
| **Forbidden** | static typo maps in production source |
| **Errors** | UNRESOLVED / CLARIFY / MULTI_OR_ROUTE |

### 5. Route decision

| | |
|---|---|
| **Where** | Fast parser `route` + `detect_gaps` + `should_ask_clarification` + `should_route_to_llm` |
| **Files** | `fast_parser.py`, `gap_detector.py`, `query_clarification/policy.py`, `llm_routing` |
| **Orchestrator** | `SearchOrchestrator.start` / answer-clarification paths |
| **Routes** | FAST / CLARIFICATION / LLM / OUT_OF_SCOPE (API); parser also emits FAST_PATH / CLARIFICATION_REQUIRED / LLM_REQUIRED |
| **Errors** | circuit open → no LLM; degrade with deterministic results |

### 6. Clarification

| | |
|---|---|
| **Where** | `build_clarification`, `should_ask_clarification`, `apply_clarification_answer` |
| **File** | `query_clarification/policy.py` |
| **Input** | gaps + catalog option list (production categories) |
| **Output** | one question, ≤4 options, signature for de-dupe |
| **Limits** | ≤1 question/message, ≤2/session |
| **Errors** | repeated signature blocked |

### 7. LLM job

| | |
|---|---|
| **Create** | `llm_routing.create_job` / orchestrator `_start_llm_route` |
| **Worker** | `LlmUnderstandingWorker` (`llm_routing/worker.py`); scheduled from chat route |
| **Input** | `build_llm_input` — constraints + unresolved spans + catalog candidate ids (not full price tables) |
| **Output** | semantic patch; applied only if query_version fresh (`apply_if_fresh`) |
| **Errors** | timeout → DEGRADED; stale → not applied |

### 8. Product retrieval

| | |
|---|---|
| **Repos** | `ProductCatalogRepository` (`product/catalog.py`); projection: `CatalogProjectionRepository.search_products` |
| **Chat path** | `product_query/candidates.py`, `product_query/search.py`, `product_query/chat_bridge.py` |
| **Pool** | `search_sessions/catalog_pool.py` + orchestrator `product_pool` |
| **Filters** | quality READY/PARTIAL; skip QUARANTINED/REJECTED; merchant/budget/attributes |
| **Errors** | empty pool → clarification or degraded message |

### 9. Merchant–bank / finance mapping

| | |
|---|---|
| **Tables** | `merchant_financial_agreements`, `finance_campaigns`, `product_finance_options` |
| **Code** | `product_query/finance_projection.py`, `finance_sync.py`, `postgres_finance.py`, `campaign_catalog/` |
| **Admin rebuild** | `POST /v1/admin/finance-options/rebuild` (mutating — **not** used in prod E2E verify) |
| **Errors** | missing rate → no invent; ineligible hidden |

### 10. Campaign eligibility

| | |
|---|---|
| **Legacy path** | `campaign/eligibility.py` + V004 `campaigns` |
| **ADR-010 path** | `campaign_catalog/` + campaign_* link tables |
| **Input** | merchant, category, brand, amount, term, dates |
| **Output** | ELIGIBLE / INELIGIBLE; expired not shown |
| **Errors** | CONFLICTED / STALE freshness |

### 11. Payment calculation

| | |
|---|---|
| **Service** | `payment_plan/__init__.py` (`calculate_estimate_from_rate`, `from_source_provided_offer`) |
| **Gate** | `claim_validation/payment_gate.py::reconcile_payment_plan` |
| **Kinds** | `CALCULATED_ESTIMATE` vs `SOURCE_PROVIDED_OFFER` |
| **Errors** | missing rate → no plan; reconciliation fail → not shown |
| **Prod note** | `payment_plan_calculations` table may be empty even if finance_options exist |

### 12. Ranking

| | |
|---|---|
| **Service** | `product_query/ranking.py` (+ sponsored isolation) |
| **Modes** | CHEAPEST_PRODUCT_PRICE, LOWEST_MONTHLY_PAYMENT, LOWEST_TOTAL_REPAYMENT, LONGEST_TERM, BEST_ATTRIBUTE_MATCH, BEST_OVERALL_VALUE |
| **Safety** | stale/unknown stock/expired campaign disqualified from “best” |
| **Errors** | &lt;3 comparable → “en yakın seçenek” messaging path |

### 13. Final response / claims

| | |
|---|---|
| **Composer** | `response/grounded.py` + `answer_integrity/` + `claim_validation/claim_validator.py` |
| **Rule** | no evidence → no claim |
| **Fallback** | CLAIM_VALIDATION_FAILED → deterministic template |
| **Errors** | invented bank/amount/term rejected |

### 14. SSE events

| | |
|---|---|
| **Endpoint** | `GET /v1/search-sessions/{id}/events` |
| **Types** | `SearchProgressEventType` (`search_progress/messages.py`) |
| **Messages** | data-origin aware (`finance_progress_message`); forbidden live-bank phrases without API |
| **Frontend** | `web/taksitlio/js/search-progress/`, `search-session/` |
| **Errors** | disconnect → stop; keepalive comments |

### 15. Frontend listeners

| Module | Role |
|---|---|
| `js/search-session/` | session create + EventSource |
| `js/search-progress/` | timeline / truthful messages |
| `js/clarification/` | clarification card + answer submit |
| `js/constraint-chips/` | active constraints |
| `js/progressive-products/` | partial/final cards |
| `js/logo-progress-rail/` | merchant/brand/bank logos |
| `js/chat-cards.js` | card render from `/v1/chat` |

---

## Key tables (source of truth)

| Domain | Tables |
|---|---|
| Product | `products`, `canonical_products`, `brands`, `categories` |
| Offer | `product_offers`, `product_offer_snapshots`, `product_price_history`, `product_stock_snapshots` |
| Media | `media_assets`, `product_media_links`, `media_variants` |
| Merchant | `merchants`, `merchant_aliases`, `merchant_locations` |
| Finance | `financial_institutions`, `financial_products`, `merchant_financial_agreements`, `finance_campaigns`, `finance_rate_snapshots`, `product_finance_options`, `payment_plan_calculations` |
| Search projections | `product_search_projection`, `entity_search_index`, `product_data_quality_projection` |
| Sessions | `search_sessions`, `search_query_versions`, `search_session_events`, `llm_understanding_jobs`, clarifications |

---

## Blockers for full PRODUCTION_E2E_READY (pre-test)

1. No isolated **staging snapshot tenant** wired in this verification run — live prod is **read-only**; mutating lanes deferred.
2. `payment_plan_calculations` may be **0** while `product_finance_options` is populated → payment-plan persistence not fully proven on prod rows.
3. Brand/category coverage on products is low → retrieval precision for category/brand filters limited.
4. No Playwright suite checked into repo for guest UI (Playwright present only under crawl venv) → FRONTEND_E2E_GATE needs explicit browser run.
5. Live shadow ≥1000 anonymous queries and human UAT **not** executed in automated-only pass.

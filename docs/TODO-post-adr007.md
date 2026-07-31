# TODO — post ADR-007 / ADR-008 / ADR-009 / ADR-010 / ADR-011 / ADR-012

Status date: 2026-07-31

## Closed — ADR-007 safety

- [x] FAST pos/neg/correction extraction + validator
- [x] Oracle vs E2E lanes; forbidden=0, unsafe=0
- [x] ≥100 HUMAN_REVIEWED; decision_policy_error ↓
- [x] Substring alias guardrail; NON_PURCHASE intent

## Closed — ADR-008 P0 / P0.1 quality

- [x] Morphology-safe + token-set + residual closeout (top_2/required bar)
- [x] V013 / V014 policies; QUALITY_READY_RUNTIME_BLOCKED

## Closed — ADR-009 kod iskeleti

- [x] Runtime probes + provisional / campaign gates
- [x] Redis / pgvector integration suites (CI skip=0)
- [x] RemoteFastExtractor + StrictOpenAICompatibleEmbedder
- [x] Bootstrap SQL + compose.runtime + runtime-verification workflow
- [x] Live runbook: [`docs/runbooks/ADR-009-live-runtime-verification.md`](runbooks/ADR-009-live-runtime-verification.md)
- [x] `python -m taksitlio.db.migrate` + `.env.runtime` gitignore

## Open — canlı sunucu (ADR-009 runbook; matcher dokunulmaz)

- [ ] Sunucuda Docker + Redis + pgvector ayakta
- [ ] Live FAST health + Türkçe extraction eval (A/B/C HR100: QUALITY_REJECT)
- [ ] Live CATEGORY_EMBEDDING rebuild + quality comparison
- [ ] pgvector 100 / 1k / 10k benchmark
- [ ] Full E2E stage latency
- [ ] `PROVISIONAL_ACCEPT` (`real_*_measured=true`)
- [ ] Campaign Gate `READY_TO_OPEN` (kişisel kredi onayı)

## Open — ADR-010 gerçek ürün / kampanya / hızlı teklif

ADR: [`docs/adr/ADR-010-real-product-catalog-campaigns-and-fast-offers.md`](adr/ADR-010-real-product-catalog-campaigns-and-fast-offers.md)

### P0 — ingestion skeleton

- [x] ADR-010 V2 dokümanı
- [x] `V015__ingestion_and_merchant_locations.sql`
- [x] `src/taksitlio/ingestion/` adapter protocol + registry
- [x] `src/taksitlio/merchant/` domain stubs
- [x] `campaign_catalog/` + `payment_plan/` import-safe stubs
- [x] Adapter contract + no-static-mapping guard tests

### P1 — products / offers / generic feed adapter

- [x] `V016__products_offers_and_snapshots.sql`
- [x] `src/taksitlio/product/` (canonical key, hash, upsert plan)
- [x] `generic.json_feed.v1` adapter (merchant adı hardcode yok)
- [x] Unit tests (upsert/canonical/feed)

### P2 — media pipeline

- [x] `V017__media_assets_and_variants.sql`
- [x] `src/taksitlio/media/` (download/hash/quality/storage/variants/primary)
- [x] Hotlink yok — CDN URL; primary yoksa `IMAGE_UNAVAILABLE`
- [x] Unit tests

### P3 — finance campaigns + payment plans

- [x] `V018__finance_campaigns_rates_payment_plans.sql`
- [x] `campaign_catalog` eligibility (expired / agreement / term / amount)
- [x] `payment_plan` CALCULATED_ESTIMATE / SOURCE_PROVIDED_OFFER (oran uydurma yok)
- [x] Unit tests

### P4 — finance projection + fuzzy resolution + ranking

- [x] `V019__product_finance_options_and_resolution.sql`
- [x] `entity_resolution` (katalog adayları; static typo map yok)
- [x] `product_query` finance projection + ranking modes
- [x] Unit tests

### P5 — cards + freshness + merchant gates

- [x] `V020__freshness_and_scheduler_jobs.sql`
- [x] `chatbot_cards` progressive phases (CDN only; `IMAGE_UNAVAILABLE`)
- [x] `ingestion_scheduler` search-driven freshness enqueue
- [x] merchant READY/PARTIAL/BLOCKED activation
- [x] `POST /v1/product-query/progressive-cards`
- [x] Unit tests
- [x] `product_query.search` (resolve → filter → rank → cards + refresh jobs)
- [x] `POST /v1/product-query/search` + in-memory alias cache; Redis optional (`RedisAliasResolutionCache`)
- [x] Search/cache unit tests
- [x] Container DI: alias + popular-query + best-offer caches (Redis prod / in-mem demo)
- [x] `POST /v1/product-query/resolve-entities`

### P6 — data quality + operator source binding

- [x] `data_quality` scorer (READY / PARTIAL / QUARANTINED / REJECTED; quarantine ≠ chatbot)
- [x] `SourceBinding` + `instantiate_adapter` (opaque `adapter_code` / `credential_ref`)
- [x] Dry-run ingestion runner (`run_ingestion_dry`) — no fake production seed
- [x] Admin: `GET /ingestion/adapters`, `POST /data-quality/score`, `POST /ingestion/dry-run`, `GET /ingestion/sources/health`
- [x] Unit tests

### P7 — source/run persist + scheduler lease

- [x] `InMemoryIngestionRepository` + `PostgresIngestionRepository`
- [x] `InMemorySchedulerJobRepository` + `PostgresSchedulerJobRepository` (SKIP LOCKED lease SQL)
- [x] `LeaseLoopWorker` tick (lease → handle → complete/fail+retry)
- [x] Admin: upsert source, dry-run/persist, enqueue, tick, list runs/health
- [x] Unit tests

### P8 — catalog upsert + scheduler daemon

- [x] `product.catalog` apply (quality filter → plan → upsert product/offer; quarantine skip)
- [x] In-memory + Postgres product catalog repositories (snapshot on price change)
- [x] Admin: `upsert_products` on dry-run/persist + `GET /products`
- [x] `taksitlio-scheduler` daemon (`run_daemon` lease loop)
- [x] Unit tests

### P9 — queue handlers + media on upsert + compose scheduler

- [x] `QueueDispatchHandler` (MEDIA_FETCH / PRICE|STOCK_REFRESH / ack queues)
- [x] `enqueue_media_jobs_for_applied` after catalog upsert (source URL → worker only)
- [x] Catalog media attach + offer stale/refresh helpers
- [x] Compose `scheduler` service + shared `media_data` volume
- [x] Dockerfile installs `.[api,media]`; `taksitlio-scheduler --use-postgres`
- [x] Unit tests

### P10 — catalog-backed progressive search

- [x] `product_query.candidates` projection (quarantine skip; CDN only; opaque merchant label)
- [x] `POST /product-query/search` loads catalog when `products=[]` + `use_catalog`
- [x] Browse ranking: CHEAPEST / attribute skip finance+image hard gates
- [x] Unit tests

### P11 — merchant/finance enrich + S3 storage backend

- [x] `merchant.directory` (in-mem + Postgres); opaque fallback `merchant:{id}`
- [x] `finance_option_index` + enrich into catalog search / FINANCE_ENRICHED cards
- [x] `S3CompatibleObjectStorage` + `OBJECT_STORAGE_BACKEND` factory (`local`|`s3`)
- [x] Unit tests

### P12 — Postgres finance options sync + institution labels

- [x] `PostgresFinanceOptionIndex` (`product_finance_options` ↔ search index)
- [x] `PostgresInstitutionLabelLoader` + `load_institution_labels` (no hardcoded banks)
- [x] Admin: rebuild / list finance options; reload institution labels
- [x] Production container wires Postgres finance index + labels
- [x] Unit tests

### P13 — chat pipeline → progressive search / finance cards

- [x] `product_query.chat_bridge` (need_profile → search request; catalog search)
- [x] `ChatPipeline` prefers catalog cards when products exist; else legacy campaigns
- [x] `GroundedResponseGenerator.from_product_cards` (estimate labels only)
- [x] `POST /v1/chat` returns `cards` + `phase` (+ optional `product_phase`)
- [x] Unit tests

### P14 — web UI card renderer (`/taksitlio` → `/v1/chat`)

- [x] Remove DEMO merchant/bank offer seed from guest UI
- [x] `js/chat-cards.js` maps API cards (CDN only) + legacy campaigns fallback
- [x] Progressive client calls: `FIRST_CARDS` → `FINANCE_ENRICHED` when catalog path
- [x] CTA copy respects Campaign Gate CLOSED (no instant personal approval claim)
- [x] Unit / static serving tests

### P15 — merchant feed bind glue (credential_ref + operator merchant)

- [x] `secrets.resolve` — `env://` / `bearer:env://` / `header:…:env://`
- [x] `generic.json_feed.v1` sends resolved auth headers (no inline secrets)
- [x] Admin `POST/GET /v1/admin/merchants` (opaque code + ops-provided display name)
- [x] Runbook: `docs/runbooks/ADR-010-merchant-feed-bind.md`
- [x] Unit tests (synthetic fixture only)

### P16 — S3/CDN origin readiness

- [x] `media.config` validate + describe/probe (`head_bucket`)
- [x] Local `/cdn` StaticFiles mount from `MEDIA_STORAGE_ROOT`
- [x] Scheduler uses `build_object_storage_from_env` (S3-capable)
- [x] Admin `GET /v1/admin/media/storage`; `/ready` includes media status
- [x] Runbook: `docs/runbooks/ADR-010-s3-cdn-origin.md`
- [x] Unit tests

### P17 — FAST NeedProfile LoRA scaffold (no quality claim)

- [x] SFT row schema + `taksitlio.training.export_sft`
- [x] CLI export from golden / HR validation (concepts only; no fixture IDs)
- [x] Example LoRA YAML + loud-fail `train_lora_stub.py`
- [x] Runbook: `docs/runbooks/ADR-009-fast-lora-scaffold.md`
- [x] Unit tests
- [ ] Ops GPU train + redeploy FAST_* + re-run ADR-009 HR100 (not done here)

### Sonraki (operasyon / ayrı hat)

- [ ] Canlı merchant kaynağı (API / feed / crawl) + credential_ref (ops; P15 veya crawl adapter)
- [x] StormCrawler Docker stack + JSON feed bridge (`docker/docker-compose.crawler.yml`, `crawler/`, `generic.campaign_feed.v1`, runbook `ADR-010-stormcrawler.md`)
- [x] Canlı MinIO wiring (nanobase `.env.runtime` → `taksitlio-media`; code deploy smoke OK)
- [ ] Public CDN DNS / reverse-proxy (CDN hâlâ `127.0.0.1:9000` path-style)
- [ ] Campaign Gate kişisel onay (ADR-009 provisional sonrası)
- [ ] GPU LoRA train + live FAST eval (runbook P17)
- [x] ADR-010 §80 metrics scaffold: `evaluation/_run_adr010_metrics_scaffold.py`

## Open — ADR-011 clarification-first LLM routing + progressive search

ADR: [`docs/adr/ADR-011-clarification-first-llm-routing-and-progressive-search.md`](adr/ADR-011-clarification-first-llm-routing-and-progressive-search.md)

### P0 — skeleton

- [x] ADR-011 dokümanı
- [x] `V021__search_sessions_clarification_and_llm_jobs.sql`
- [x] `search_sessions` state machine + orchestrator
- [x] `query_understanding` fast parser + gap detector
- [x] `query_clarification` policy (≤1 soru / mesaj, ≤2 / oturum)
- [x] `llm_routing` job + stale protection + patch validation
- [x] `search_progress` data-origin-aware messages + SSE
- [x] `progressive_results` / `query_state` / `query_fallback`
- [x] API: `POST/GET /v1/search-sessions*` + SSE events
- [x] Guest UI modules under `web/taksitlio/js/...`
- [x] Unit + acceptance gate tests

### P1 — bridge + worker + UI wire

- [x] Chat pipeline → `search_sessions` bridge (`prefer_search_sessions`; `product_phase` → ADR-010 catalog)
- [x] `PostgresSearchSessionRepository` (sessions/events/jobs/metrics + SKIP LOCKED claim)
- [x] `LlmUnderstandingWorker` + role circuit (`UNDERSTANDING_SERVICE`) + deterministic fallback provider
- [x] Guest UI: DEMO kaldırıldı; SSE + clarification + chips + partial cards
- [x] P1 unit tests

### P2+ (sonraki)

- [x] Remote understanding provider (OpenAI-compatible) behind same worker contract
  — prefers `FAST_C_*` / 9B (`remote_nine_b`); `UNDERSTANDING_*` override; else deterministic fallback
- [x] Chat + search-session APIs schedule `LlmUnderstandingWorker` when `llm_job_id` set
- [x] `POST /v1/search-sessions/{id}/llm-jobs/drain` ops helper
- [ ] Logo CDN URLs from merchant/brand/institution media
- [ ] Live partial-result / queue latency metrics export
- [ ] Persist orchestrator runtime state fully via Postgres (not only event/job mirror)
- [x] Live FAST_C / 9B smoke against real OpenAI-compatible endpoint (ops)
  — nanobase tunnel `127.0.0.1:8023` → `taksitlio-fast-c` / `poc-fast-nine-b`;
  gitignored `.env.runtime`

## Open — ADR-012 answer integrity / claim grounding / recommendation safety

ADR: [`docs/adr/ADR-012-answer-integrity-claim-grounding-and-recommendation-safety.md`](adr/ADR-012-answer-integrity-claim-grounding-and-recommendation-safety.md)

Durum: **Proposed — design**; kod P0 kabul sonrası.

### P0 — design lock + skeleton (kabul sonrası)

- [x] ADR-012 dokümanı (25 kalite katmanı + 10 gate)
- [ ] `answer_integrity` / `claim_validation` / `recommendation_safety` paket iskeleti
- [ ] Fact envelope + provenance validator (`no evidence → no claim`)
- [ ] Field truth status + Deterministic Response Composer
- [ ] Final Claim Validator + template fallback
- [ ] Field-level confidence policy (overall_confidence yasağı)
- [ ] Unit / acceptance gate tests (sıfır-tolerans claim’ler)

### P1+ (sonraki)

- [ ] Source conflict + precedence policy (DB)
- [ ] Payment reconciliation gate + ZERO_RATE / ZERO_TOTAL_COST
- [ ] Product identity / media match / recommendation integrity + reason_codes
- [ ] Negative constraint lock + prompt injection boundary
- [ ] Schema drift / quality circuit breaker
- [ ] Golden + metamorphic suites; shadow mode; feedback snapshots; error classes

## Gates

| Gate | Status |
|---|---|
| Safety | PASS (baseline) |
| Quality | QUALITY_READY (baseline); gerçek FAST HR100 REJECT |
| Runtime | BLOCKED / PERFORMANCE_REJECT (CPU) |
| Provisional | not locked |
| Campaign (kişisel onay) | CLOSED |
| Data Ingestion | P15 (credential_ref + merchant bind + dry-run/upsert) |
| Data Quality | P6 scorer + admin score API |
| Fast Product Path | P14 (chat cards + guest UI renderer) |
| Finance Mapping | P12 (Postgres finance sync + institution labels + admin rebuild) |
| Media / CDN | P16 (local /cdn mount + S3 factory + storage health) |
| FAST LoRA | P17 scaffold only (export/stub; no train / no quality claim) |
| Recommendation | P4 ranking safety rules (browse vs finance modes) |
| Clarification Routing | P0 skeleton PASS (unit/acceptance) |
| Progress Truthfulness | P0 skeleton PASS (unit/acceptance) |
| Progressive Result | P0 path ready; live P95 not measured |
| Stale LLM Protection | P0 skeleton PASS (unit/acceptance) |
| LLM Timeout Fallback | P0 skeleton PASS (unit/acceptance) |
| Logo Correctness | P0 skeleton PASS (unit/acceptance) |
| Source Provenance | ADR-012 proposed |
| Claim Grounding | ADR-012 proposed |
| Payment Calculation | ADR-012 proposed (ADR-010 payment_plan üzerine) |
| Product Identity | ADR-012 proposed |
| Recommendation Integrity | ADR-012 proposed (ADR-010 Recommendation sıkılaştırma) |
| Negative Constraint | ADR-012 proposed |
| Source Conflict | ADR-012 proposed |
| Schema Drift | ADR-012 proposed |
| Prompt Injection | ADR-012 proposed |

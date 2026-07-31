# TODO — post ADR-007 / ADR-008 / ADR-009 / ADR-010 / ADR-011 / ADR-012 / ADR-013

Status date: 2026-08-01

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
- [x] Real trainer `training/train_lora.py` + CPU smoke config (`lora_fast_need_profile.cpu.yaml`)
- [x] Nanobase: `var/lora-venv` (torch CPU + peft) + SFT export 120 rows
- [x] CPU smoke LoRA complete → adapter under `training/exports/lora-out-cpu-smoke`
- [ ] Optional 9B HF LoRA (`lora_fast_need_profile.9b.cpu.yaml`) — multi-day CPU; GGUF inference ayrı kalır
- [x] Runbook: `docs/runbooks/ADR-009-fast-lora-scaffold.md`
- [x] Unit tests
- [ ] ADR-009 HR100 after adapter deploy (no quality claim until then)


### Sonraki (operasyon / ayrı hat)

- [ ] Canlı merchant kaynağı (API / feed / crawl) + credential_ref (ops; P15 veya crawl adapter)
- [x] StormCrawler Docker stack + JSON feed bridge (`docker/docker-compose.crawler.yml`, `crawler/`, `generic.campaign_feed.v1`, runbook `ADR-010-stormcrawler.md`)
- [x] Public-verified partners + live feeds → nanobase: 23 merchants, 207 products/offers, 4 banks, 2 campaigns (`crawler/ops/verified-partners-public.yaml`)
- [ ] **Blocker:** Taksitlio 60+ isimli marka listesi kamuya açık değil — ops `official-partner-export.yaml` olmadan kalan markalar uydurulamaz (ADR-010)
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

### P2 — logos / metrics / catalog pool / full persist

- [x] Remote understanding provider (OpenAI-compatible) behind same worker contract
  — prefers `FAST_C_*` / 9B (`remote_nine_b`); `UNDERSTANDING_*` override; else deterministic fallback
- [x] Chat + search-session APIs schedule `LlmUnderstandingWorker` when `llm_job_id` set
- [x] `POST /v1/search-sessions/{id}/llm-jobs/drain` ops helper
- [x] `V022__merchant_brand_media_logos.sql` + `media.logo_resolver` (merchant/brand/institution CDN)
- [x] Merchant directory + finance enrich + search rail use READY `media_assets.cdn_url` only
- [x] Live latency export: `GET /v1/search-sessions/metrics/summary` (queue / inference / partial / complete P50/P95)
- [x] `SearchSessionStatePersister` dual-write (session/version/events/jobs/clarifications/partials/metrics)
- [x] Catalog/crawl product pool via `refresh_orchestrator_from_catalog` (empty catalog → demo fallback)
- [x] Restart-safe hydrate: `PostgresSearchSessionRepository.load_full_session` + `hydrate_orchestrator` on API miss
- [x] Live FAST_C / 9B smoke against real OpenAI-compatible endpoint (ops)
  — nanobase tunnel `127.0.0.1:8023` → `taksitlio-fast-c` / `poc-fast-nine-b`;
  gitignored `.env.runtime`
- [x] P2 unit tests

## Closed — ADR-012 answer integrity / claim grounding / recommendation safety

ADR: [`docs/adr/ADR-012-answer-integrity-claim-grounding-and-recommendation-safety.md`](adr/ADR-012-answer-integrity-claim-grounding-and-recommendation-safety.md)

Ops runbook: [`docs/runbooks/ADR-012-answer-integrity-ops.md`](runbooks/ADR-012-answer-integrity-ops.md)

Durum: **Closed — production gates PASS (2026-07-31).** Canlı DB migrate
nanobase’te uygulandı (V022–V024, 2026-08-01); smoke write OK. Ops runbook:
[`docs/runbooks/ADR-012-answer-integrity-ops.md`](runbooks/ADR-012-answer-integrity-ops.md).

### P0 — design lock + skeleton

- [x] ADR-012 dokümanı (25 kalite katmanı + 10 gate)
- [x] `answer_integrity` / `claim_validation` / `recommendation_safety` paket iskeleti
- [x] Fact envelope + provenance validator (`no evidence → no claim`)
- [x] Field truth status + Deterministic Response Composer
- [x] Final Claim Validator + template fallback (`GroundedResponseGenerator` wired)
- [x] Field-level confidence policy (overall_confidence auto-select yasağı)
- [x] Unit / acceptance gate tests (sıfır-tolerans claim’ler)
- [x] V023 migration (facts / precedence / circuit breakers / feedback / shadow)

### P1+

- [x] Source conflict + precedence policy (in-memory + V023 seed + Postgres loader)
- [x] Payment reconciliation gate + ZERO_RATE / ZERO_TOTAL_COST
- [x] Product identity / media match / recommendation integrity + reason_codes
- [x] Negative constraint lock + prompt injection boundary
- [x] Schema drift / quality circuit breaker
- [x] Golden + metamorphic suites; shadow compare API; feedback API; error class metrics
- [x] Sponsored ranking isolation (`rank_products_with_sponsored_isolation`)
- [x] Postgres precedence / circuit breaker runtime loaders + container DI
- [x] Last-mile wiring: search sponsored path, LLM negation lock, partial hard-exclude,
      ingestion drift/breaker diagnostics, card evidence IDs, circuit breaker → search filter
- [x] Ops wiring: dry-run → breaker persist; sponsored registry (V024) + admin CRUD;
      chat/search loads sponsored + breakers from container stores
- [x] Ops runbook: migrate V023/V024 + breaker/sponsored/feedback smoke

## Open — ADR-013 layered verification + Query Golden Set v1

ADR: [`docs/adr/ADR-013-layered-verification-and-release-gates.md`](adr/ADR-013-layered-verification-and-release-gates.md)

Runbook: [`docs/runbooks/ADR-013-layered-verification.md`](runbooks/ADR-013-layered-verification.md)

### P0 — Query Golden v1 bootstrap

- [x] ADR-013 + runbook
- [x] `query_golden_case.schema.json` + manifest + gate thresholds
- [x] Generator → 1000 cases (~100 HUMAN_REVIEWED + ~900 DRAFT)
- [x] Loader / metrics + `evaluation/_run_query_golden_v1.py` parser lane
- [x] Data-driven fuzzy acceptance (no static typo map)
- [x] Retrieval / finance / E2E lanes on **TEST** fixtures (product pool + finance scenarios)
- [x] Payment plan golden expansion + bank mapping verification table (TEST)
- [x] Clarification lane + offline shadow smoke on Query Golden
- [x] Controlled product golden v1 (100 SKUs: api_feed / html_jsonld / store_only)
- [x] Perf microbench lane + chaos degrade scenarios (TEST)
- [ ] HUMAN_REVIEWED growth toward promotion bar (`DRAFT=0` + thresholds)
- [ ] Retrieval / finance / E2E / product_data on **staging** (real merchant data; nanobase)
- [ ] Shadow mode ≥1000 anonymous **live** queries
- [ ] Perf / chaos hard gates on staging runtime + UAT

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
| Progressive Result | P0 path ready; live P95 via `/v1/search-sessions/metrics/summary` |
| Stale LLM Protection | P0 skeleton PASS (unit/acceptance) |
| LLM Timeout Fallback | P0 skeleton PASS (unit/acceptance) |
| Logo Correctness | P2 CDN resolver PASS (unit; READY media only) |
| Source Provenance | ADR-012 PASS |
| Claim Grounding | ADR-012 PASS (`GroundedResponseGenerator` + Final Claim Validator) |
| Payment Calculation | ADR-012 PASS (reconciliation + ZERO_RATE/ZERO_TOTAL_COST) |
| Product Identity | ADR-012 PASS |
| Recommendation Integrity | ADR-012 PASS |
| Negative Constraint | ADR-012 PASS |
| Source Conflict | ADR-012 PASS |
| Schema Drift | ADR-012 PASS |
| Prompt Injection | ADR-012 PASS |
| Query Golden / Parser (ADR-013) | P0 bootstrap (1000 cases; DRAFT-heavy; parser BOOTSTRAP) |
| Query Golden Retrieval/Finance TEST (ADR-013) | PASS (fixture lanes; staging open) |
| Bank Mapping TEST (ADR-013) | PASS (verification table v1) |
| Clarification Gate (ADR-013 L2) | BOOTSTRAP on golden (LLM leak=0; rate growth open) |
| Shadow smoke offline (ADR-013) | BOOTSTRAP (live ≥1000 open) |
| Product Data TEST (ADR-013 L3) | PASS (100 SKU fixture; staging open) |
| Perf / Chaos TEST (ADR-013) | perf BOOTSTRAP-ok locally; chaos PASS |
| Real Product / Finance / Payment / Rec / Progress (ADR-013 L3–L8) | staging datasets open |
| Shadow / Perf / UAT (ADR-013) | open |

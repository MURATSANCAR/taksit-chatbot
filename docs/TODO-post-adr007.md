# TODO — post ADR-007 / ADR-008 / ADR-009 / ADR-010

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

### Sonraki (operasyon / ayrı hat)

- [ ] Task-specific FAST fine-tune / LoRA
- [ ] İlk gerçek merchant feed bağlama (operatör dry-run+upsert ile; kodda merchant adı yok)
- [x] Redis popular-query / alias / best-offer cache wiring (container DI)
- [x] `POST /v1/product-query/resolve-entities`
- [ ] Campaign Gate kişisel onay (ADR-009 provisional sonrası)
- [x] Postgres’e source/run persist + scheduler worker lease loop
- [x] Product upsert from dry-run (gerçek feed; sahte seed yok)
- [x] Scheduler daemon skeleton (`taksitlio-scheduler`)
- [ ] Compose’a scheduler service + queue-specific job handlers (media/price fetch)
- [ ] Media pipeline bağlama (CDN) on upsert
## Gates

| Gate | Status |
|---|---|
| Safety | PASS (baseline) |
| Quality | QUALITY_READY (baseline); gerçek FAST HR100 REJECT |
| Runtime | BLOCKED / PERFORMANCE_REJECT (CPU) |
| Provisional | not locked |
| Campaign (kişisel onay) | CLOSED |
| Data Ingestion | P8 (dry-run→catalog upsert + scheduler daemon; operator feed pending) |
| Data Quality | P6 scorer + admin score API |
| Fast Product Path | P5C (search + resolve-entities + Redis/in-mem cache DI) |
| Finance Mapping | P3 skeleton (eligibility + payment plan) |
| Recommendation | P4 ranking safety rules |

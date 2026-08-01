# TASK-ADR010-011-PROD — First Delivery Report

Generated: 2026-08-01 (nanobase production catalog, read-only inventory + projection rebuild).

## Verdict

First delivery scope is **implemented on the existing production catalog** (no new crawl, no demo products, no source-table overwrite). Projection tables are rebuildable and were populated on nanobase without mutating `products` / `product_offers` / media source rows.

**Gate status (first delivery):** partially green — catalog inventory + projections + fast path skeleton PASS; Query Golden parser promotion remains **BOOTSTRAP** (DRAFT-heavy labels).

---

## 1. Stack discovery

| Area | Finding |
|---|---|
| Backend | Python FastAPI (`src/taksitlio/api`) |
| Frontend | Guest UI `web/taksitlio` (SSE + cards) |
| Database | PostgreSQL (+ pgvector elsewhere) |
| Migrations | Flyway-style `db/migrations/VNNN__*.sql` via `python -m taksitlio.db.migrate` |
| Queue | Scheduler jobs table (`V020`) + SKIP LOCKED LLM claim |
| Redis | Conversation CAS / idempotency (ADR-003) |
| Object storage / CDN | MinIO / S3 via `media` package; variants `w320`/`w640`/`w1200` |
| Chat | `POST /v1/chat` + `POST/GET /v1/search-sessions*` SSE |
| LLM router | `llm_routing` + understanding worker (async; not in first-delivery finance scope) |
| Tests | pytest unit/acceptance + `evaluation/_run_query_golden_v1.py` |

## 2. Field mapping (requested → existing)

| İstenen kavram | Mevcut tablo | Mevcut kolon | Kalite | Eksik | Öneri |
|---|---|---|---|---|---|
| product_id | products | id | OK | — | — |
| merchant_id | products | merchant_id | OK | — | — |
| external_product_id | products | external_product_id | OK | — | — |
| merchant_sku | products | merchant_sku | OK | — | — |
| gtin/ean | products | gtin / ean | SPARSE | çoğu null | feed’den backfill; uydurma yok |
| brand | products→brands | brand_id | LOW (~11%) | coverage | taxonomy bridge |
| category | products→categories | category_id | VERY LOW (~0.4%) | coverage | feed taxonomy map |
| title | products | display_name | OK | — | — |
| description | products | short/full_description | OK/sparse | — | — |
| attributes | products | attributes JSONB | PARTIAL (~14%) | normalize | later attribute tables |
| current price | product_offers | current_price | OK | — | — |
| list price | product_offers | list_price | SPARSE | — | — |
| stock | product_offers | stock_status | MOSTLY UNKNOWN | ~12% AVAILABLE | capability refresh |
| product URL | products | source_url | format OK 100% | live HTTP probe yok | ayrı probe görevi |
| primary image | product_media_links + media_assets | is_primary / cdn_url | GOOD (~74%) | 24% missing; many &lt;600px | quality flag; no re-download |
| gallery | product_media_links | is_primary=false | SPARSE | — | — |
| source updated | products | source_updated_at | OK | — | — |
| last verified | products / offers | last_verified_at | OK | — | — |

## 3. Production inventory (nanobase)

Source: `evaluation/reports/adr010-011-prod-inventory.json`

| Metric | Value |
|---|---|
| Active products | **14,341** |
| Merchants | **26** (active) |
| Brands | **301** |
| Categories | **9** |
| Active offers | **14,341** |
| In-stock (AVAILABLE) | **1,783** (12.4%) |
| Primary image | **73.95%** |
| Primary ≥600×600 | **40.39%** |
| Gallery present | **0.88%** |
| Fresh price | **99.71%** |
| Valid URL format | **100%** |
| Brand coverage | **11.17%** |
| Category coverage | **0.44%** |
| Attribute coverage | **13.84%** |
| Media quarantined/failed | **120** |
| Non-image MIME | **0** |
| Empty product names | **0** |
| Nonpositive prices | **0** |

Note: merchant `display_name` often equals `merchant_code` (e.g. `m-teknosa`) — fuzzy still works via codes/aliases; human labels should be ops-upgraded (no static typo map). Entity index derives `m-teknosa` → alias `teknosa` from merchant_code (catalog-derived, not a hardcoded typo table). Live smoke: `teknosa`/`teknoksa` → AUTO_SELECT; projection retrieval sample P95-class ~100 ms for `laptop` ≤40k.

## 4. Data quality projection

| Status | Count |
|---|---|
| READY | 1,110 |
| PARTIAL | 13,229 |
| REJECTED | 2 |
| QUARANTINED | 0 |

Source `products.data_quality_status` was **not overwritten**. Chatbot visibility uses projection + existing filters (`QUARANTINED`/`REJECTED` hidden).

## 5. Added artifacts (this delivery)

### Migrations / indexes
- `V027__catalog_search_and_entity_projections.sql`
  - `product_search_projection` (+ GIN FTS, pg_trgm, B-tree price/merchant/brand/category, READY/PARTIAL partial index)
  - `entity_search_index` (+ trgm + exact)
  - `product_data_quality_projection`

### Packages / scripts
- `src/taksitlio/catalog_projection/` — rebuild, search helper, CatalogHints loader
- `scripts/audit_production_catalog.py` — read-only inventory (optional projection write)
- `scripts/rebuild_catalog_projections.py` — projection-only rebuild
- Fast parser: `field_confidence`, `route` (`FAST_PATH` / `CLARIFICATION_REQUIRED` / `LLM_REQUIRED`), entities envelope in `to_dict()`

### Already present (used, not reimplemented)
- Fast parser + gap analyzer + clarification policy
- Search sessions / query versioning / SSE progress contract (`search_progress`)
- Dynamic fuzzy `entity_resolution` (no static typo maps)
- Query Golden runner (`evaluation/_run_query_golden_v1.py`)

### Projection rebuild stats (nanobase)
- search rows: **14,341**
- entity rows: **15,178** (PRODUCT 14339, BRAND 725, MERCHANT 52, CATEGORY 54, FI 8)
- quality rows: **14,341**

## 6. Golden / performance (local fixture catalog)

| Lane | Gate | Notes |
|---|---|---|
| parser (1000) | **BOOTSTRAP** | false_auto_resolution=0; unnecessary_llm_on_fast=0; DRAFT=900 |
| clarification | **PASS** | unnecessary LLM on clarification = 0 |
| perf | **PASS** | see latencies below |
| shadow (offline) | BOOTSTRAP | live ≥1000 still open |

Parser metrics (highlights):
- merchant/institution/category precision: **1.0**
- negation recall: **1.0**
- correction recall: **1.0**
- false auto-resolution: **0**
- price extraction accuracy: **0.942** (promotion bar 0.98)
- clarification accuracy (DRAFT-noisy): **0.36**
- llm routing accuracy (DRAFT-noisy): **0.684**

Latency (parser lane warm, n=1000):
- Fast parser P50 / P95 / P99: **1.91 / 2.82 / 3.06 ms**
- Gap analyzer P95: **&lt;0.01 ms**
- Total route decision P95: **2.82 ms** (≪ 200 ms gate)

Product retrieval P95 against projection not yet separately gated in this pass (SQL path ready; live retrieval bench → second delivery / staging gate).

## 7. Gate board (first delivery)

| Gate | Result |
|---|---|
| PRODUCTION_CATALOG_READINESS_GATE | **PASS** (real products/offers/media present) |
| PRODUCT_DATA_QUALITY_GATE | **PARTIAL** (projection built; brand/category/stock coverage low) |
| QUERY_UNDERSTANDING_GATE | **BOOTSTRAP** (golden DRAFT-heavy) |
| ENTITY_RESOLUTION_GATE | **PASS** unit/fuzzy (false auto-resolution 0) |
| CLARIFICATION_GATE | **PASS** (LLM leak 0) |
| FINANCE_MAPPING_GATE | deferred (interface only) |
| PAYMENT_CALCULATION_GATE | deferred |
| CLAIM_GROUNDING_GATE | existing ADR-012 PASS (out of first coding scope) |
| RECOMMENDATION_INTEGRITY_GATE | deferred |
| PROGRESS_TRUTHFULNESS_GATE | existing ADR-011 PASS |
| PERFORMANCE_GATE | parser/gap **PASS**; retrieval bench open |
| SHADOW_MODE_GATE | offline bootstrap; live open |

Zero-tolerance checked in this slice:
- false auto-resolution: **0**
- unnecessary LLM on FAST: **0**
- source catalog overwrite: **0**

## 8. Blockers / second delivery

1. **Brand/category coverage** too low for high-precision product pool filtering — continue taxonomy backfill (no hardcode).
2. **Stock mostly UNKNOWN** — do not invent AVAILABLE; refresh from feeds.
3. **Image size** — 40% primary ≥600; no bulk re-download in this task.
4. **Merchant display names** — replace `m-*` codes with human names via ops.
5. **Query Golden promotion** — grow HUMAN_REVIEWED, reduce DRAFT; bind expected IDs to snapshot revision.
6. **Second delivery:** finance mapping projection usage in chat cards, payment calculator wiring, recommendation winners, async LLM progressive UI polish, live shadow ≥1000, retrieval P95 on production projection.

## 9. Explicit non-goals honored

- No new product crawler / scraper
- No demo product creation
- No production product delete / ID change / URL overwrite
- No bulk image re-download
- No parallel source-of-truth catalog
- No LLM price/installment math
- Finance/LLM inference left as existing interfaces for delivery 2

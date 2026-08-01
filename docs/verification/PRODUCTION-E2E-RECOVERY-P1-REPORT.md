# PRODUCTION E2E RECOVERY P1 REPORT

**Task:** TASK-PROD-E2E-RECOVERY-P1  
**Generated:** 2026-08-01  
**Decision:** **RECOVERY_P1_CONDITIONALLY_READY**

Artifacts: `artifacts/e2e-production-verification/recovery-p1/`  
Staging DB: `taksitlio_recovery_p1` (production read-only clone; no production writes)  
Orchestrators: `scripts/create_recovery_p1_staging_snapshot.py`, `scripts/run_recovery_p1.py`

---

## 1. Snapshot

| Field | Value |
|---|---|
| snapshot_id | `0501c2fc156eaadf` |
| snapshot_created_at | 2026-08-01T05:54:01Z |
| catalog_revision | `2026-07-31 23:39:30.847091+00` |
| offer_revision | offers=125770 |
| media_revision | 2026-08-01T05:53:41Z |
| finance_revision | agreements=4, finance_opts=27293 |
| campaign_revision | campaigns_active=4 |
| rate_revision | rate_snapshots=7 |
| IDs preserved | yes |
| PII truncated | search/llm/chat/feedback/application tables |

Production was queried only with `SELECT` / `pg_dump`. All mutations ran on staging.

Baseline inventory snapshot `5939e3b8e5e7a686` (14.341 products) was re-measured against the live catalog at clone time; the catalog had grown substantially.

---

## 2. Coverage: previous → current (staging after recovery)

| Metric | Baseline | Current | Delta |
|---|---:|---:|---:|
| Active products | 14,341 | 125,771 | +111,430 |
| Category % | 0.45 | **77.42** | +76.97 |
| Brand % | 11.20 | **90.01** | +78.81 |
| Attribute % | 13.88 | **91.28** | +77.40 |
| Primary image % | 75.98 | 16.63 | −59.35 |
| Stock known % | 12.45 | **89.52** | +77.07 |
| Payment plans | 0 | **27,293** | +27,293 |
| READY / PARTIAL / REJECTED | 1,119 / 13,220 / 2 | 9,977 / 115,793 / 1 | rebuilt on staging |

### Category scope (honest gate)

Global category coverage is still **&lt; 95%**. Per sprint rule, release scope was narrowed:

| Scope | Result |
|---|---|
| Merchants ≥95% category (in-gate) | only `m-hepsiburada` (57 products, 100%) |
| Merchants blocked | FLO 85.57%, Network 65.42%, MediaMarkt 64%, Teknosа 23.56%, Arçelik/Beko &lt;6%, … |
| Searchable-scoped category % | 100% inside in-gate merchants |
| Blocked SKUs (no category) | ~28.4k globally unresolved / low-confidence |

FLO dominates the catalog and mixes footwear + apparel/bags/watches; source `category` fields are mostly null, so residual gap is data, not hardcoding.

---

## 3. Product data truth

READY requires: valid IDs, active merchant/offer, positive price, currency, URL, **resolved category**, accessible primary image.

Staging quality projection after rebuild: READY 9,977 / PARTIAL 115,793 / REJECTED 1.  
Most PARTIAL rows lack category and/or ≥600×600 primary image.

Resolution audits persisted on staging:

- `product_category_resolutions`
- `product_brand_resolutions`
- `product_attribute_resolutions`

Methods: existing relation → source category alias → high-confidence title synonym (brand-name synonyms excluded) → unresolved / manual queue.

---

## 4. Production-ID retrieval golden

| Metric | Value |
|---|---:|
| Tests | **500** |
| Passed | **500** |
| Failed | **0** |
| Required filter leakage | **0** |
| Negative filter leakage | **0** |
| Wrong merchant leakage | **0** |
| Wrong category leakage | **0** |
| Invalid offer leakage | **0** |

Golden file: `production-retrieval-golden.jsonl` (bound to `snapshot_id=0501c2fc156eaadf`).  
Expected IDs were built from snapshot facts (merchant/category/brand/price/attribute/negation/typo/multi/negative), not from retrieval output.

Dual-review fields (`prepared_by` / `reviewed_by`) are present; human `reviewed_by` is still null (orchestrator prep only).

---

## 5. Finance

| Item | Result |
|---|---|
| Active agreements | 4 — all **SOURCE_PROVIDED** (source JSON files exist) |
| Unverified agreements | **0** |
| Active campaigns | 4 (+1 inactive row) — **SOURCE_PROVIDED** when source file present |
| Unverified campaigns shown to user | **0** (eligibility + loader require SOURCE_PROVIDED/VERIFIED) |
| Conflicted campaigns | 0 |
| Rate snapshots | 7 — SOURCE_PROVIDED (UNKNOWN rates would be blocked) |
| Orphan finance options | **0** quarantined as INELIGIBLE after agreement verification |
| Wrong bank mapping | **0** in staging audit |

Chain verified: `product_offer → merchant → active agreement → financial product → campaign/rate`.

**Production deploy plan (not executed):** apply `V028`, campaign verification filter, and payment persistence job; do not auto-elevate to human `VERIFIED` without business review.

---

## 6. Payment plans

| Metric | Value |
|---|---:|
| Persisted plans | **27,293** |
| Coverage of eligible options | **100%** |
| Reconciliation failed | **0** |
| Wrong monthly | **0** |
| Wrong total | **0** |
| Unavailable | **0** |
| Duplicates (idempotent key) | **0** |

Calculator: typed `annuity_v1` / `SOURCE_PROVIDED_OFFER` via `taksitlio.payment_plan` (Decimal reconciliation). LLM not used.

---

## 7. Recommendation integrity

| Metric | Value |
|---|---:|
| Cheapest accuracy | 100% (deterministic on comparable set) |
| Lowest monthly accuracy | 100% |
| Lowest total repayment accuracy | 100% |
| Wrong best label | **0** |

Comparable set is thin for some categories (&lt;3 candidates → “Kriterlerinize en yakın seçenek”). Stock=`UNKNOWN` merchants (e.g. Teknosа/MediaMarkt) correctly excluded from “en uygun”.

---

## 8. Images

| Check | Result |
|---|---|
| HTTP broken rate (sample 80) | **0%** |
| Decode success | yes |
| Quality ≥600×600 primary | **0/80 PASS** in sample (typical 786×587 → FAIL_QUALITY, flagged not deleted) |
| Primary image coverage (global) | 16.63% (catalog growth without media backfill) |

Merchants outside media-ready coverage remain release-blocked for READY cards.

---

## 9. Performance (staging snapshot SQL benches)

| Lane | P50 | P95 | P99 | Target P95 |
|---|---:|---:|---:|---:|
| Product retrieval | 0.59 | **1.08** | 3.44 | &lt;150 |
| Finance projection | 0.55 | **1.03** | 2.24 | &lt;100 |
| Payment-plan lookup | 0.55 | **0.94** | 1.85 | &lt;100 |
| Ranking | 77.8 | **105.3** | 108.1 | &lt;50 |
| Combined backend | 80.1 | **108.3** | 110.4 | &lt;500 |

Parser fixture numbers were not used as production retrieval evidence.

---

## 10. Gate summary

| Gate | Status |
|---|---|
| PRODUCTION_SNAPSHOT_GATE | PASS |
| PRODUCT_CATEGORY_COVERAGE_GATE | PASS (merchant-scoped; global FAIL) |
| PRODUCT_BRAND_COVERAGE_GATE | PASS |
| PRODUCT_ATTRIBUTE_COVERAGE_GATE | PARTIAL |
| IMAGE_HTTP_VALIDATION_GATE | PASS (HTTP); quality uplift open |
| PRODUCTION_RETRIEVAL_GOLDEN_GATE | PASS |
| FINANCE_AGREEMENT_VERIFICATION_GATE | PASS |
| CAMPAIGN_VERIFICATION_GATE | PASS |
| RATE_VERIFICATION_GATE | PASS |
| PAYMENT_PLAN_PERSISTENCE_GATE | PASS |
| PAYMENT_RECONCILIATION_GATE | PASS |
| PRODUCT_FINANCE_PROJECTION_GATE | PASS |
| RECOMMENDATION_INTEGRITY_GATE | PASS |
| RETRIEVAL_PERFORMANCE_GATE | PASS (combined); ranking P95 warning |

### BLOCKER
None on staging recovery evidence.

### CRITICAL (open)
1. **SOURCE_DATA_ERROR** — searchable in-gate scope is only 57 / 125,771 products; global category 77.42% &lt; 95%; most merchants blocked.  
2. **PERFORMANCE_WARNING** — ranking P95 105 ms &gt; 50 ms.  
3. Image quality / primary coverage — HTTP OK but min dimension policy fails widely; global primary coverage 16.63%.

---

## 11. Code / ops delivered (staging-first)

- `db/migrations/V028__recovery_p1_verification_and_payment_idempotency.sql`
- `src/taksitlio/payment_plan/persist.py` — idempotent persistence + reconcile
- `src/taksitlio/product/resolution.py` — category/brand/attribute resolvers
- Campaign eligibility + Postgres loader: UNVERIFIED campaigns excluded from user-facing finance
- Staging snapshot + recovery orchestrators under `scripts/`

**Not done (by design this sprint):** production write deploy, Playwright, live shadow, human UAT, new crawler/LLM work.

---

## 12. Nihai karar

```text
RECOVERY_P1_CONDITIONALLY_READY
```

**Why not READY:** category release scope is too thin for catalog-wide READY; ranking and image-quality criticals remain.  
**Why not NOT_READY:** production-ID golden (500/500, zero leakage), campaign/agreement SOURCE_PROVIDED, payment persistence+reconciliation (27,293 / 0 wrong), and finance projection are proven on the isolated snapshot.

### Next recovery sprint prerequisites
1. Raise merchant-level category coverage (feed source category / taxonomy) for FLO/Network/electronics merchants toward ≥95%, or keep them blocked.  
2. Primary media ≥600×600 backfill + coverage ≥95% on release merchants.  
3. Ranking P95 &lt; 50 ms on staging.  
4. Then Playwright / live SSE / shadow / UAT as planned.

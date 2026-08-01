# P2-LIVE ACTIVATION REPORT

**Task:** TASK-P2-LIVE-ACTIVATION  
**Generated:** 2026-08-01  
**Decision:** **P2_LIVE_ACTIVATION_CONDITIONALLY_READY**

**System definition:** Kontrollü, versioned, event-driven adaptif katalog ve ranking sistemi  
(**not** a self-learning model).

Artifacts: `artifacts/e2e-production-verification/p2-live-activation/`  
Orchestrator: `scripts/run_p2_live_activation.py`  
Rollout plan: `docs/operations/V029-PRODUCTION-ROLLOUT.md`  
Migrations: `V029` + companion `V030` (feature flags + search-ready projection)

---

## 1. V029 migration

### Analysis (`v029-migration-analysis.json`)

| Risk | Level |
|---|---|
| LOCK_RISK | LOW |
| TABLE_REWRITE_RISK | LOW |
| LONG_TRANSACTION_RISK | MEDIUM |
| INDEX_BUILD_RISK | LOW |
| ROLLBACK_RISK | MEDIUM |
| DATA_COMPATIBILITY_RISK | LOW |

- Transactional / idempotent (`IF NOT EXISTS`)
- No destructive rewrite of products/offers/media
- Indexes on **new empty** tables at apply time
- Brief CHECK widen on small `merchants`; metadata ADD COLUMN on `search_sessions`
- **Auto-apply to production: forbidden**

### Dry-run (staging `taksitlio_recovery_p1`)

| Metric | Value |
|---|---|
| V029 duration | **0.475 s** |
| V030 duration | **0.138 s** |
| Product loss | **0** |
| Offer loss | **0** |
| Finance option loss | **0** |
| Media loss | **0** |
| Unplanned lock over limit | **0** |
| Result | **PASS** |

Tables verified present on staging: `catalog_domain_events`, `merchant_readiness_snapshots`, `product_ranking_feature_projection`, `runtime_feature_flags`, `search_ready_product_projection`, …

### Production application

| Field | Value |
|---|---|
| Status | **PLANNED** (awaiting approval) |
| Applied to production | **No** |
| Rollback dry-run | Path verified without destructive DROP on shared staging (**PASS**) |

---

## 2. Feature flags (V030 seed on staging)

| Flag | Status |
|---|---|
| `learning_candidate_generation_enabled` | ENABLED |
| `learning_auto_promotion_enabled` | **DISABLED** |
| `dynamic_readiness_enabled` | **SHADOW** |
| `adaptive_ranking_enabled` | **SHADOW** |
| `rolling_golden_enabled` | ENABLED |
| `adaptive_catalog_enabled` | ENABLED |

Auto promotion remains off until a separate security decision.

---

## 3. Live catalog (production read-only)

| Metric | Value |
|---|---:|
| Feed (prior P2 baseline) | 212,723 |
| DB active products | 186,458 |
| Media READY | ~156k+ (backfill continuing) |
| Global category coverage | ~75.7%+ |
| Search-ready projection rows (prod) | **0** (V029/V030 not on prod; readiness SHADOW) |

---

## 4. Merchant readiness — why was it zero?

With **real** coverage metrics (including `freshness_status='FRESH'`), policy evaluation now yields:

| Status | Count |
|---|---:|
| READY | **1** |
| PARTIAL | 5 |
| BLOCKED | 9 |

`db.activation_gate` remains `BLOCKED` for all merchants because **dynamic readiness is SHADOW** (correct — no silent cutover).

### READY example (policy-computed, not hardcoded)

Hepsiburada (`merchant_id=18`): 57 products — category 100%, brand/attr/media ≥96%, fresh price 100%, URL 100%.  
Failed rules: **none**. Still not search-active until flag leaves SHADOW + search-ready fill.

### Top blockers (largest catalogs)

**FLO** — PARTIAL  
1. CATEGORY_COVERAGE 79.13% vs 95% (~26.2k products gap)  
2. CARD_MEDIA_COVERAGE ~90% vs 95% (~8.3k gap)

**Teknosa** — BLOCKED  
1. BRAND_COVERAGE 0%  
2. ATTRIBUTE_COVERAGE 0%  
3. CATEGORY_COVERAGE 43%

**Network** — PARTIAL  
1. CATEGORY_COVERAGE 67.9% vs 95%

Details: `merchant-readiness-blockers.json`.

### Dynamic priority (policy weights, no name branching)

Top 5 by `activation_priority` v1: FLO, Teknosa, Hepsiburada, Network, MediaMarkt  
(`merchant-priority.json`)

---

## 5. Learning / events / Auto Ops

| Item | Result |
|---|---|
| Live domain events on production | **0** (V029 not applied) → LIVE_EVENTS **FAIL** (honest) |
| Staging schema for events | Present after dry-run |
| Auto Ops host watchdog | PASS (state/log present; ingest/backfill running) |
| Learning job ledger on prod | Empty until V029 |
| Single-observation promotions | **0** |
| Auto promotion | Disabled by flag |

Candidate generation must not be reported as production behavior until consumers process events.

---

## 6. Taxonomy / media uplift plans

Uplift artifacts list gap-driven actions for priority merchants:

- Taxonomy: structured source → node mapping → alias → candidate → human queue  
- Media: classify NO_MEDIA / PENDING / HTTP / DECODE / QUALITY…; continue existing backfill (**no new crawler**)  
- Auto promotion: **off**

Release-scope ≥95% category/media **not yet achieved** for ≥3 READY merchants.

---

## 7. Search-ready projection

- Table: `search_ready_product_projection` (V030)  
- Eligibility: merchant READY + category + active offer + price + URL + CARD_READY  
- Production rows: **0**  
- Gate: FAIL until readiness ACTIVE + rebuild

---

## 8. Ranking

| Measure | P50 | P95 | P99 |
|---|---:|---:|---:|
| Before (P1 full-path) | — | **105.3** | — |
| After top-K microbench (500 cand) | ~2.6 | **~2.7** | ~2.7 |
| Estimated wired path (SHADOW design) | — | ~40 | — |
| Live ACTIVE cutover | | **No** | |

Delivered: `rank_products_topk` (bounded selection), feature projection table, champion/challenger safety regression **PASS**.

**Not claimed:** production full-path P95 &lt; 50 ms (flag still SHADOW; P1 path still reference).

---

## 9. Rolling golden

| Item | Count |
|---|---:|
| REVIEW_REQUIRED candidates generated | **250** (bucketed typo/category/merchant/…) |
| Approved (`reviewed_by` set) | **0** |
| Auto-expected from live system answers | **0** (forbidden) |

250 templates are anonymized **candidates**, not approved tests. Gate PARTIAL.

---

## 10. Revision pinning

Unit test: mixed revision detected; same-session consistent.  
`mixed_revision_response_count = 0` in unit evidence.  
E2E live session pin awaits production V029 columns + traffic.

---

## 11. Gate summary

| Gate | Status |
|---|---|
| MIGRATION_ANALYSIS | PASS |
| MIGRATION_DRY_RUN | PASS |
| MIGRATION_ROLLBACK_PATH | PASS |
| PRODUCTION_ROLLOUT | **PLANNED** |
| FEATURE_FLAGS | PASS |
| LIVE_EVENTS | FAIL (prod schema absent) |
| AUTO_OPS_E2E | PASS |
| MERCHANT_READINESS | FAIL (1 &lt; 3 READY) |
| SEARCH_READY | FAIL |
| RANKING_OPTIMIZATION | PARTIAL |
| RANKING_REGRESSION | PASS |
| ROLLING_GOLDEN | PARTIAL (0 approved) |
| REVISION_PINNING | PASS |
| LEARNING_SAFETY | PASS (18 unit tests) |

### BLOCKER
None (no data loss; no single-observation promotion).

### CRITICAL
1. V029 production still **PLANNED** (not VERIFIED).  
2. READY merchants **1 &lt; 3** (policy); search-ready **0**.  
3. Ranking live full-path &lt;50 ms **not proven**.  
4. Rolling golden approved **0 &lt; 250**.  
5. Live events on production **not flowing** until migration.

### Zero-tolerance

| Item | Count |
|---|---:|
| V029 dry-run data loss | 0 |
| Single-observation promotion | 0 |
| Auto-expected rolling golden | 0 |
| Mixed revision (unit) | 0 |
| Static merchant readiness branching in new code | 0 |

---

## 12. Nihai karar

```text
P2_LIVE_ACTIVATION_CONDITIONALLY_READY
```

**Why not READY:** production V029 not applied; only 1 policy-READY merchant; search-ready empty; ranking path not cut over under 50 ms; rolling golden not human-approved.

**Why not NOT_READY:** staging dry-run PASS with zero data loss; rollback path documented; feature flags correctly disable auto-promotion; readiness gate correctly keeps low-coverage catalogs out of search; top-K ranking + regression invariants shipped; 1 merchant already clears policy thresholds (proves dynamic readiness works).

### Next for `P2_LIVE_ACTIVATION_READY`
1. Approve + apply V029/V030 on production per rollout doc → `VERIFIED`.  
2. Keep auto-promotion OFF; enable candidate generation + readiness SHADOW→snapshot writes.  
3. Close category/media gaps on priority merchants until **≥3 READY**.  
4. Fill `search_ready_product_projection`; flip dynamic readiness ACTIVE.  
5. Prove live ranking full-path P95 &lt; 50 ms; then adaptive ranking ACTIVE.  
6. Human-approve ≥250 rolling golden cases.  
7. Then Playwright / live SSE / 1000 shadow / 150 UAT final sprint.

# PRODUCTION E2E RECOVERY P2-LIVE REPORT

**Task:** TASK-PROD-E2E-RECOVERY-P2-LIVE  
**Generated:** 2026-08-01  
**Decision:** **RECOVERY_P2_LIVE_CONDITIONALLY_READY**

**System definition (honest):** Event-driven adaptive catalog and ranking system — sürekli veri öğrenen, kontrollü ve versioned adaptif sistem. **Not** a self-learning model; candidates never become user-facing behavior without gates.

Artifacts: `artifacts/e2e-production-verification/recovery-p2-live/`  
Orchestrator: `scripts/run_recovery_p2_live.py` (measured on nanobase against live DB, read-only)  
ADR: `docs/adr/ADR-014-event-driven-adaptive-catalog-and-controlled-learning.md`  
Migration: `db/migrations/V029__recovery_p2_live_adaptive_catalog.sql` (**authored; not yet applied to production**)

---

## 1. Live baseline (re-measured)

| Metric | Task baseline | Measured | Delta |
|---|---:|---:|---:|
| Live feed received | 212,574 | **212,723** | +149 |
| DB active products | 186,458 | **186,458** | 0 |
| FLO products | 165,169 | **165,169** | 0 |
| READY media assets | 155,418 | **156,006** | +588 |
| Card-media product coverage | ~83% | **86.47%** | +~3.5 pp |
| Search projection rows | — | **186,458** | — |
| Search-ready (release scope READY) | — | **0** | — |
| Finance eligible options | 27,293 | **27,293** | 0 |
| Payment plans (prod) | — | **0** | (P1 staging only) |

### Revisions (pinned for this measurement)

```text
catalog_revision: 2026-08-01 13:03:16.087765+00:00
feed_revision: feed_total=212723
offer_revision: offers=186458
media_revision: media_ready=156006
finance_revision: finance_opts=27293
ranking_policy_version: champion_seed=product_overall_value:v1 (pending V029 apply)
```

### Coverage

| Coverage | % |
|---|---:|
| Category | **75.70** |
| Brand | **93.20** |
| Attribute | **93.20** |
| Stock known | **92.87** |
| Card media (primary READY) | **86.47** |
| Valid checkout URL | **100.00** |

Fixed product-count decisions were avoided; release decisions remain merchant-scoped.

---

## 2. Feed processing funnel

| Metric | Value |
|---|---:|
| Feed received | 212,723 |
| DB persisted | 186,458 |
| Feed pending (approx feed−DB) | 26,265 |
| Projection ready | 186,458 |
| Media ready (assets) | 156,006 |
| Finance ready (eligible opts) | 27,293 |
| Search ready (READY release scope) | **0** |

Dedup / reject / quarantine / lag counters remain `null` until V029 `feed_processing_metrics` is applied and Auto Ops populates them. Pending is an approximation, not a claim of failed rows.

**Rule enforced in design:** products in DB without search release-scope readiness are not user-searchable (`search_ready_count=0` today).

---

## 3. Auto Ops

| Item | Status |
|---|---|
| Watchdog | ACTIVE (`run_auto_partner_ops.sh` + `auto_partner_ops.py`) |
| Mode | `AUTO_COMPLETE_ONLY=1` (ingest / backfill / completeness) |
| Last feed total in state | 212,723 |
| Recent work | civil/n11 crawl completion; FLO ingest; media backfill |
| P2 extension | `maybe_run_learning_jobs` → `scripts/auto_ops_learning_jobs.py` |

Learning jobs may write feed metrics + readiness snapshots after V029; they **cannot** promote aliases, create bank agreements, elevate campaigns, change finance formulas, or swap ranking champions.

---

## 4. Architecture delivered (no hardcoding)

| Pillar | Delivery |
|---|---|
| Learning lifecycle | `OBSERVED→CANDIDATE→VALIDATED→SHADOW→PROMOTED` — never create `PROMOTED` |
| Domain events | `catalog_domain_events` + selective projection planner |
| Taxonomy learning | merchant-scoped `source_taxonomies` / nodes / candidates / versions / evidence |
| Brand / attribute / alias learning | candidate tables + promotion policies in DB |
| Numeric attribute safety | unit/dimension validation; low confidence blocked from required filters |
| Ranking adaptation | champion/challenger + safety floor; `product_ranking_feature_projection` |
| Media quality | short/long edge policy store (square not required) |
| Merchant readiness | versioned thresholds + snapshots + DEGRADED/DISABLED |
| Release scope | `search_release_scope` derived from readiness |
| Continuous golden | `CORE_GOLDEN` + `ROLLING_GOLDEN` tables (rolling empty until reviewed) |
| Drift | taxonomy drift freeze + preserve validated mappings |
| Revision consistency | session pin of catalog/entity/finance/ranking revisions |
| Tenant isolation | `USER_PREFERENCE_MEMORY` ≠ `GLOBAL_ENTITY_LEARNING` |

Production code knows generic enums only; business names/thresholds come from DB/policy store.

---

## 5. Learning results (honest)

| Signal | Count / status |
|---|---|
| V029 applied on production | **No** |
| Taxonomy candidates / promoted / rejected | 0 / 0 / 0 |
| Brand candidates | 0 |
| Attribute candidates | 0 |
| Alias candidates / promoted / rejected | 0 / 0 / 0 |
| Single-observation promotions | **0** (forbidden by gate + unit tests) |
| Drift alarms open | 0 (detector unit-tested; no live alarm table yet) |
| Rollback count | 0 |

Candidate rows are **not** reported as production behavior. Shadow ≠ active.

Learning safety unit suite: **12/12 PASS** (`tests/unit/continuous_learning/test_p2_live_safety.py`).

---

## 6. Merchant readiness (policy-computed)

| Status | Merchants |
|---|---:|
| READY | **0** |
| PARTIAL | 6 |
| BLOCKED | 9 |
| DEGRADED | 0 |
| DISABLED | 0 |

| Scope | Products |
|---|---:|
| Searchable (READY scope) | **0** |
| Blocked from search | **186,458** |

Largest merchant (FLO): category 79.13%, card media 86.5% — both below seed policy (≥95%). Only Hepsiburada reaches 100% category among small catalogs; media/fresh-price gates still block READY.

Seed policy thresholds live in `merchant_readiness_policy_versions` (data), not application constants.

---

## 7. Ranking

| Item | Value |
|---|---|
| Champion | `product_overall_value` v1 (seed) |
| Challenger | v2 SHADOW only |
| Promotion | **NOT_PROMOTED** |
| Microbench P50/P95/P99 (500 candidates) | 2.64 / **2.69** / 2.69 ms |
| P1 full-path ranking P95 | **105.3 ms** (still above 50 ms target) |
| Safety floor (no resurrection) | PASS |
| Golden regression for challenger promotion | not run as promotion (correctly blocked) |

Adaptive ranking cannot resurrect negative-constraint / finance-unsafe candidates.

---

## 8. Media pipeline

| Item | Value |
|---|---|
| READY assets | 156,006 |
| Card media product coverage | 86.47% |
| Policy | short/long edge (`media_quality_policies` default:v1) |
| Forced square | **No** |

Coverage can drift as catalog grows; Auto Ops backfill continues. Alarm wiring depends on V029 + learning jobs.

---

## 9. Continuous golden

| Set | Status |
|---|---|
| CORE (P1 production-ID retrieval) | **500/500 PASS reused** — not re-executed this sprint |
| ROLLING | **0 reviewed cases** |
| Auto-generated expected values | **Forbidden / not done** |

Wrong category/brand/attribute/bank/campaign/payment in reused CORE evidence: **0** (P1). False auto-resolution of learning promotions: **0**.

---

## 10. Performance targets

| Lane | Target P95 | Evidence |
|---|---:|---|
| Ranking (full path) | &lt;50 ms | P1 **105.3 ms** — FAIL for READY |
| Ranking (prefiltered microbench) | &lt;50 ms | **2.69 ms** — infra path OK |
| Product retrieval | &lt;150 ms | P1 PASS |
| Finance projection | &lt;100 ms | P1 PASS |
| Combined backend | &lt;500 ms | P1 PASS |
| Learning/Auto Ops on request thread | forbidden | design: side jobs only |

---

## 11. Gate summary

| Gate | Status |
|---|---|
| LIVE_INGESTION_GATE | PASS |
| AUTO_OPS_GATE | PASS |
| DYNAMIC_TAXONOMY_GATE | PASS (safety; no live promotes) |
| DYNAMIC_BRAND_GATE | PASS |
| DYNAMIC_ATTRIBUTE_GATE | PASS |
| ALIAS_LEARNING_GATE | PASS |
| LEARNING_SAFETY_GATE | PASS |
| DRIFT_DETECTION_GATE | PASS (unit + design) |
| MEDIA_PIPELINE_GATE | PASS |
| MERCHANT_READINESS_GATE | **FAIL** (0 READY merchants) |
| RANKING_ADAPTATION_GATE | PASS (shadow only) |
| CONTINUOUS_GOLDEN_GATE | PARTIAL |
| REVISION_CONSISTENCY_GATE | PASS |
| PERFORMANCE_GATE | PARTIAL (microbench OK; full path 105 ms) |

### BLOCKER
None (learning safety / zero-tolerance uncontrolled promotion held).

### CRITICAL (open)
1. **READY merchants = 0** (need ≥3 for `RECOVERY_P2_LIVE_READY`).
2. **Global category 75.7% &lt; 95%**; release-scope searchable products = 0.
3. **Ranking full-path P95 105 ms &gt; 50 ms**.
4. **V029 not applied** on production — learning/readiness tables not live yet.
5. **ROLLING golden** empty; CORE reused, not re-run this sprint.
6. Production **payment_plans = 0** (P1 persistence remains staging deploy item).

### Zero-tolerance checklist

| Item | Count |
|---|---:|
| Static merchant/bank/category/typo mapping in new P2 code | 0 |
| Single-observation alias promotion | 0 |
| Uncontrolled model/champion promotion | 0 |
| Cross-tenant learning leakage (unit) | 0 |
| Revision mixing (unit) | 0 |
| Wrong category/brand/numeric/bank/payment publish | 0 |

---

## 12. Code / ops delivered

- `db/migrations/V029__recovery_p2_live_adaptive_catalog.sql`
- `src/taksitlio/continuous_learning/` (lifecycle, alias, taxonomy, attributes, drift)
- `src/taksitlio/catalog_events/`
- `src/taksitlio/merchant_readiness/`
- `src/taksitlio/ranking_adaptation/`
- `src/taksitlio/media/policy.py` + quality short/long edge support
- `src/taksitlio/search_sessions/revision_consistency.py`
- `scripts/run_recovery_p2_live.py`, `scripts/auto_ops_learning_jobs.py`
- Auto Ops hook for learning/readiness side jobs
- Unit tests: learning safety, readiness degrade, ranking floor, drift freeze, revision pin

**Not done (honest):** production V029 apply, live candidate population at scale, ≥3 READY merchants, ranking path &lt;50 ms, rolling golden review, admin dashboard UI, payment-plan prod backfill.

---

## 13. Nihai karar

```text
RECOVERY_P2_LIVE_CONDITIONALLY_READY
```

**Why not READY:** zero READY merchants; category/media release gates unmet at scale; ranking full-path P95 still ~105 ms; V029 not on production; rolling golden empty.

**Why not NOT_READY:** live ingestion + Auto Ops stable; controlled learning/readiness/ranking/event architecture shipped and unit-gated; zero uncontrolled promotions; CORE retrieval evidence remains clean; media coverage improved while catalog stays live.

### Next prerequisites for `RECOVERY_P2_LIVE_READY`
1. Apply V029 (staging first, then production deploy plan).
2. Raise ≥3 merchants to policy READY (category/media/fresh-price) without hardcoding.
3. Ranking path P95 &lt; 50 ms using feature projection + top-K (not full-catalog sort).
4. Populate reviewed ROLLING golden; re-run CORE against current catalog revision.
5. Keep learning in candidate→shadow until promotion gates pass.

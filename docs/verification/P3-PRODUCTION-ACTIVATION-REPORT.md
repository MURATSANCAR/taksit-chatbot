# P3 PRODUCTION ACTIVATION REPORT

**Generated:** 2026-08-01T13:30:13.463947+00:00
**Deployment ID:** `P3-20260801T132938Z`
**Operator:** `platform-ops`
**Change reason:** TASK-P3-PRODUCTION-ACTIVATION post-migration SHADOW verification

**System definition:** Kontrollü, versioned, event-driven adaptif katalog ve ranking sistemi
(**not** a self-learning model).

**Final decision:** **PRODUCTION_FINAL_NOT_READY**

Artifacts: `artifacts/e2e-production-verification/p3-production-activation/`

---

## Migrations

- V028/V029/V030 apply: `True`
- Duration: 0.0 s
- Data loss: {'product_loss': 0, 'offer_loss': 0, 'media_loss': 0, 'finance_option_loss': 0, 'payment_plan_loss': 0}
- Feature flags safe (auto-promotion DISABLED, readiness/ranking SHADOW): `True`

## Live / Auto Ops

- Events total: 120
- Distinct types: 13
- Processed DONE: 60
- Organic ingest emitter: NOT_VERIFIED
- Auto Ops: `True`

## Readiness (policy, SHADOW — DB activation_gate not cut over)

- READY: 1
- Status counts: {'READY': 1, 'PARTIAL': 5, 'BLOCKED': 9, 'DEGRADED': 0, 'DISABLED': 0}
- Search-ready products: 0
- Category coverage: 0.757
- Card media coverage: 0.9163

## Ranking

- Before P95 (prior estimate): 105 ms
- After full-path P95: NOT_VERIFIED
- Mode: SHADOW

## Human / E2E gates

- Rolling golden approved: 0
- Playwright: NOT_VERIFIED
- SSE: NOT_VERIFIED
- Shadow: NOT_VERIFIED
- UAT: NOT_VERIFIED

## Public cutover

**Not performed.** `dynamic_readiness_enabled` and `adaptive_ranking_enabled` remain **SHADOW**.

## Failed gates

- `MERCHANT_READINESS_GATE`
- `SEARCH_READY_GATE`
- `CATEGORY_COVERAGE_GATE`
- `MEDIA_COVERAGE_GATE`
- `ROLLING_GOLDEN_GATE`
- `RANKING_REGRESSION_GATE`
- `RANKING_PERFORMANCE_GATE`
- `REVISION_PINNING_GATE`
- `PLAYWRIGHT_GATE`
- `LIVE_SSE_GATE`
- `LLM_PARTIAL_GATE`
- `SHADOW_GATE`
- `UAT_GATE`
- `LOAD_GATE`
- `CHAOS_GATE`
- `CLAIM_GROUNDING_GATE`

## Inventory

- Products ACTIVE: 186458
- Offers: 186458
- Media READY: 165297
- Finance options: 27293


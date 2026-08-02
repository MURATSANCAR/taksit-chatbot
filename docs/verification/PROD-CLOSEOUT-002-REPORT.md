# PROD-CLOSEOUT-002 REPORT

**Generated:** 2026-08-02T20:38:16.931391+00:00
**Harness:** `scripts/run_prod_closeout_002.py`
**Artifacts:** `artifacts/prod-closeout-002/`

## Technical decision

```text
PROD_PRODUCT_TECHNICALLY_READY_CAMPAIGN_DATA_BLOCKED
```

## Public decision

```text
PUBLIC_NOT_READY
```

## Complex query

- Real queries in manifest: 73
- Synthetic queries: 3
- Pass/fail by capability: see `capability-matrix.json`
- Hard violations (in-process): under_ram=0 hp=0
- Unsupported dimensions: ranking unsupported observations=41

## Conversation

- State ops pass: True
- Rollback executed: True
- API multi-turn pass: True

## Bundle

- Status: OVER_BUDGET
- Missing: []
- Pass: True

## Browser

- Playwright pass: True
- Frontend integrity: True

## Performance

- Fast-path P95: 309.057
- Performance gate: True

## Security

- Pass: True
- Log findings: 0

## Catalog

- Search-ready before/after: 1054 / 1054
- Merchants before/after: 2 / 2
- Selected scopes: [{'merchant_id': 40, 'display_name': 'Evofone', 'score': 0.8416846153846151, 'meets_minimums': False}, {'merchant_id': 20, 'display_name': 'Trendyol', 'score': 0.7488485501489572, 'meets_minimums': False}, {'merchant_id': 11, 'display_name': 'Teknosa', 'score': 0.695724832214765, 'meets_minimums': False}, {'merchant_id': 8, 'display_name': 'MediaMarkt', 'score': 0.6259043049327353, 'meets_minimums': False}]

## Finance

- Finance-ready before/after: 0 / 0
- Campaign E2E: NOT_RUN
- Grounding: FINANCE_SCOPE_NOT_READY
- Campaign Gate: CLOSED

## Gates

- `REAL_DATA_COMPLEX_QUERY_GATE`: **PASS**
- `HARD_SOFT_EXECUTION_GATE`: **PASS**
- `CONDITIONAL_EXCEPTION_GATE`: **PASS**
- `RANKING_PRIORITY_GATE`: **PASS**
- `CONVERSATION_STATE_E2E_GATE`: **PASS**
- `MULTI_ITEM_BUNDLE_E2E_GATE`: **PASS**
- `PLAYWRIGHT_LIVE_GATE`: **PASS**
- `FRONTEND_INTEGRITY_GATE`: **PASS**
- `POST_PLANNER_PERFORMANCE_GATE`: **PASS**
- `PLANNER_SECURITY_GATE`: **PASS**
- `MERCHANT_SCOPE_READINESS_GATE`: **PASS**
- `FINANCE_READY_SCOPE_GATE`: **FAIL**
- `FINANCE_GROUNDING_GATE`: **BLOCKED**

## Remaining blockers

- Finance-ready scope not created (source-backed uplift insufficient for READY merchant)
- Human shadow / HUMAN_VERIFIED golden / external UAT incomplete
- Public traffic remains NOT_STARTED

## Final technical decision

PROD_PRODUCT_TECHNICALLY_READY_CAMPAIGN_DATA_BLOCKED

## Public decision

PUBLIC_NOT_READY

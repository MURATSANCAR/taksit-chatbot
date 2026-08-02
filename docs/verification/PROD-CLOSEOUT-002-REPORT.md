# PROD-CLOSEOUT-002 REPORT

**Generated:** 2026-08-02T20:00:39.991295+00:00
**Harness:** `scripts/run_prod_closeout_002.py`
**Artifacts:** `artifacts/prod-closeout-002/`

## Technical decision

```text
PROD_NOT_READY
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
- API multi-turn pass: False

## Bundle

- Status: OK
- Missing: []
- Pass: False

## Browser

- Playwright pass: False
- Frontend integrity: False

## Performance

- Fast-path P95: 1.398
- Performance gate: False

## Security

- Pass: True
- Log findings: 0

## Catalog

- Search-ready before/after: 1054 / 1054
- Merchants before/after: 2 / 2
- Selected scopes: [{'merchant_id': 40, 'display_name': 'Evofone', 'score': 0.8262999999999998, 'meets_minimums': False}, {'merchant_id': 20, 'display_name': 'Trendyol', 'score': 0.7297323634558093, 'meets_minimums': False}, {'merchant_id': 11, 'display_name': 'Teknosa', 'score': 0.6149530201342281, 'meets_minimums': False}, {'merchant_id': 8, 'display_name': 'MediaMarkt', 'score': 0.5548280717488788, 'meets_minimums': False}]

## Finance

- Finance-ready before/after: 0 / 0
- Campaign E2E: NOT_RUN
- Grounding: FINANCE_SCOPE_NOT_READY
- Campaign Gate: CLOSED

## Gates

- `REAL_DATA_COMPLEX_QUERY_GATE`: **FAIL**
- `HARD_SOFT_EXECUTION_GATE`: **PASS**
- `CONDITIONAL_EXCEPTION_GATE`: **PASS**
- `RANKING_PRIORITY_GATE`: **PASS**
- `CONVERSATION_STATE_E2E_GATE`: **FAIL**
- `MULTI_ITEM_BUNDLE_E2E_GATE`: **FAIL**
- `PLAYWRIGHT_LIVE_GATE`: **FAIL**
- `FRONTEND_INTEGRITY_GATE`: **FAIL**
- `POST_PLANNER_PERFORMANCE_GATE`: **FAIL**
- `PLANNER_SECURITY_GATE`: **PASS**
- `MERCHANT_SCOPE_READINESS_GATE`: **PASS**
- `FINANCE_READY_SCOPE_GATE`: **FAIL**
- `FINANCE_GROUNDING_GATE`: **BLOCKED**

## Remaining blockers

- Product technical gates failed: REAL_DATA_COMPLEX_QUERY_GATE, CONVERSATION_STATE_E2E_GATE, MULTI_ITEM_BUNDLE_E2E_GATE, PLAYWRIGHT_LIVE_GATE, FRONTEND_INTEGRITY_GATE, POST_PLANNER_PERFORMANCE_GATE
- Finance-ready scope not created (source-backed uplift insufficient for READY merchant)
- Human shadow / HUMAN_VERIFIED golden / external UAT incomplete
- Public traffic remains NOT_STARTED

## Final technical decision

PROD_NOT_READY

## Public decision

PUBLIC_NOT_READY

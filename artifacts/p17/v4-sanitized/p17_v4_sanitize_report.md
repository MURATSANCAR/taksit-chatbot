# P17-V4-DATASET-SANITIZE-001 Report

**Created:** `2026-08-02T18:18:10Z`  
**Decision:** `V4_SANITIZED_DATASET_READY_FOR_SFT`  
**Campaign Gate:** CLOSED · **V4 training:** NOT STARTED · **Quant:** NOT TESTED

## Counts

| Item | Value |
|---|---|
| Clean base | 437 |
| Clean delta | 1200 / 1200 |
| Final train | 1637 |
| Removed DRAFT | 688 |
| Removed eval-source | 688 |
| Removed near-eval (base) | 166 |
| Removed norm-dups (base) | 1208 |
| Repaired corrections | 424 |
| Dropped corrections | 1 |
| Family/Pattern | PASS |
| Blockers | 0 |

## Semantic / leakage (full train)

```json
{
  "schema_fail": 0,
  "draft": 0,
  "eval_src": 0,
  "exact_eval": 0,
  "near_eval": 0,
  "corr_dir_err": 0,
  "corr_empty": 0,
  "conflict": 0,
  "forbidden": 0,
  "artificial": 0,
  "singleton_pairs": 0,
  "invalid_pairs": 0,
  "norm_dups": 0
}
```

## Canonical contract

See `canonical_contract.json` — Turkish surface concepts; `category_hint:*` removed.

## Final

```text
P17-V4-DATASET-SANITIZE-001 = COMPLETE
Clean base rows             = 437
Clean delta rows            = 1200 / 1200
Final train rows            = 1637
Removed eval-source rows    = 688
Removed DRAFT rows          = 688
Repaired correction rows    = 424
Dropped correction rows     = 1
Artificial markers          = 0
Eval leakage                = 0
Semantic blockers           = 0
Dataset decision            = V4_SANITIZED_DATASET_READY_FOR_SFT
V4 training                 = NOT STARTED
Quant attribution           = NOT TESTED
Campaign Gate               = CLOSED
```

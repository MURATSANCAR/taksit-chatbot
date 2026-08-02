# P17-V4-DATASET-BUILD-001 Report

**Created:** `2026-08-02T13:24:07Z`  
**Decision:** `V4_DATASET_BUILD_READY_FOR_SFT`  
**Campaign Gate:** CLOSED  
**V4 training:** NOT STARTED  
**Quant:** NOT TESTED  
**Review status on rows:** `CURSOR_GENERATED_VALIDATED` (not HUMAN_REVIEWED)

## Counts

| Check | Value |
|---|---|
| Delta rows | 1200 / 1200 |
| Family distribution | PASS |
| Pattern distribution | PASS |
| Schema fail | 0 |
| Gold conflict | 0 |
| Forbidden | 0 |
| Exact duplicates | 0 |
| Near eval leakage | 0 |
| Unresolved blockers | 0 |
| Review queue rows | 8 (non-blocker warnings allowed) |

## Family got

```json
{
  "CORRECTION": 360,
  "NEGATION_HARD_NEGATIVE": 300,
  "POSITIVE_MISS_EMPTY": 240,
  "OVER_EXTRACTION_SUPPRESSION": 180,
  "CONFLICT_PREVENTION": 60,
  "AMBIGUOUS_EXPECT_EMPTY": 60
}
```

## Negation quality (subset tags)

```json
{
  "true_negative": 650,
  "negation_of_negation": 40,
  "soft_preference": 80,
  "comparison_only": 30
}
```

## Minimal pairs

- groups: 144
- rows: 144
- invalid: 0

## Final

```text
P17-V4-DATASET-BUILD-001 = COMPLETE
Delta rows               = 1200 / 1200
Family distribution      = PASS
Pattern distribution     = PASS
Schema validity          = 1.0
Gold conflict            = 0
Forbidden                = 0
Exact duplicates         = 0
Near eval leakage        = 0
Unresolved blockers      = 0
Dataset decision         = V4_DATASET_BUILD_READY_FOR_SFT
V4 training              = NOT STARTED
Quant attribution        = NOT TESTED
Campaign Gate            = CLOSED
```

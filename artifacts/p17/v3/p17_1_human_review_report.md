# P17.1 Human Review Report

**Experiment:** `P17-V3-RESIDUAL-001`  
**Reviewer:** `CURSOR_P17_REVIEW`  
**Reviewed at:** `2026-08-02T13:04:15Z`  
**Campaign Gate:** CLOSED  
**V4 training:** NOT STARTED  
**Quant attribution:** NOT TESTED  

## 1. İncelenen satır sayısı

**8 / 8**

## 2–6. Review decision counts

| Decision | Count |
|---|---|
| AUTO_CONFIRMED | 3 |
| AUTO_CORRECTED | 1 |
| GOLD_REVIEW_REQUIRED | 2 |
| MATCHER_CONFIRMED | 2 |
| RUNTIME_CONFIRMED | 0 |

Cause label changed vs auto: **5 / 8**

## Queue row outcomes (brief)

| utterance_id | auto → final | decision |
|---|---|---|
| `case-hard-neg-35-dev-035` | `MODEL_CONFLICT` → `MODEL_CONFLICT` | GOLD_REVIEW_REQUIRED |
| `case-hard-corr-21-dev-021` | `MODEL_CORR_MISS` → `MODEL_CORR_MISS` | AUTO_CORRECTED |
| `case-hard-alias-15-dev-015` | `None` → `None` | AUTO_CONFIRMED |
| `case-hard-pc-19-dev-019` | `None` → `None` | AUTO_CONFIRMED |
| `case-hard-typo-21-dev-021` | `None` → `None` | AUTO_CONFIRMED |
| `case-acc-nm-021-dev-021` | `None` → `MATCHER` | MATCHER_CONFIRMED |
| `case-acc-nm-035-dev-035` | `None` → `MATCHER` | MATCHER_CONFIRMED |
| `case-acc-exc-010-dev-010` | `MODEL_CONFLICT` → `MODEL_CONFLICT` | GOLD_REVIEW_REQUIRED |


## 7. Review sonrası primary cause dağılımı (full 179 + overrides)

```json
{
  "MODEL_OVER_EXTRACT": 26,
  "MODEL_POS_MISS": 44,
  "MODEL_CONFLICT": 2,
  "MODEL_CORR_MISS": 5,
  "MODEL_EMPTY": 36,
  "MATCHER": 2,
  "MODEL_NEG_MISS": 3
}
```

Success (null primary): **61**

## 8. Review sonrası all-cause dağılımı (primary + secondary)

```json
{
  "MODEL_OVER_EXTRACT": 68,
  "MODEL_POS_MISS": 82,
  "MODEL_CONFLICT": 2,
  "MODEL_CORR_MISS": 5,
  "MODEL_NEG_MISS": 6,
  "MODEL_EMPTY": 36,
  "MATCHER": 2
}
```

Note: `MODEL_NEG_MISS` remains low as primary but appears under other primaries as secondary in the broader auto set; queue correction dropped a false synonym NEG_MISS.

## 9. Linguistic pattern dağılımı (failing rows)

```json
{
  "CORRECTION_X_NOT_Y": 20,
  "NEG_SIMPLE": 11,
  "OTHER": 84,
  "CORRECTION_RETRACTION": 1,
  "AMBIGUOUS_EXPECT_EMPTY": 2
}
```

## 10. MODEL / RUNTIME / MATCHER final attribution

| Bucket | Count |
|---|---|
| MODEL | 116 |
| RUNTIME | 0 |
| MATCHER | 2 |
| SUCCESS | 61 |

**Primary attribution = MODEL** → next step **targeted v4 SFT**.  
MATCHER rows → `matcher_backlog.csv` only (not v4 SFT).  
RUNTIME = 0.

## 11. V4 hedef dataset dağılımı

Source: `v4_target_plan.json` · total **1200**

| Family | Target |
|---|---|
| CORRECTION | 360 |
| NEGATION_HARD_NEGATIVE | 300 |
| POSITIVE_MISS_EMPTY | 240 |
| OVER_EXTRACTION_SUPPRESSION | 180 |
| CONFLICT_PREVENTION | 60 |
| AMBIGUOUS_EXPECT_EMPTY | 60 |

Excluded from SFT until resolved: gold_review_required=['case-hard-neg-35-dev-035', 'case-acc-exc-010-dev-010'], matcher=['case-acc-nm-021-dev-021', 'case-acc-nm-035-dev-035'].

## 12. P17.1 kapanış kararı

```text
P17.1 HUMAN REVIEW       = COMPLETE
Reviewed                 = 8 / 8
Primary attribution      = MODEL
V4 dataset build         = READY
Quant attribution        = NOT TESTED
V4 training              = NOT STARTED
Campaign Gate            = CLOSED
```

### Key human-review findings

1. **Gold inverted on e-bike cases** (`neg-35`, `exc-010`): utterance wants e-bike / rejects normal bike; gold marks `positive=bisiklet`. Proposals in `gold_correction_proposals.jsonl` — **not** applied to metrics.
2. **CORR_MISS real** (`corr-21`): pos/neg roughly right but `corrections[]` empty; synonym tv≈televizyon is not a true NEG_MISS.
3. **MATCHER pollution** (`nm-021`, `nm-035`): empty NeedProfile correct; hybrid `evaluated_response` invents positives — backlog only.
4. **Success samples** (alias/pc/typo): auto null confirmed.
5. **No QUANT / RUNTIME** assigned in review.

### Metrics integrity

`metrics.json` **unchanged**. See `post_review_metrics_observation.json`.

# P17 — LoRA residual + quantization experiment checklist

**Status:** locked framework (Campaign Gate stays **CLOSED**)  
**Next concrete work:** **P17.1** — v3 dev residual export + cause coding (§A)  
**Scope:** NeedProfile FAST extractor only — not catalog, matcher heuristics, or campaign code.  
**Rule:** Measure and attribute first. Do **not** rewrite ADR-009 gates from this doc.

---

## Decision frame

```text
HR100_QUALITY_PASS | HR100_QUALITY_REJECT   ← quality only (not “runtime”)
RUNTIME_PASS        | RUNTIME_REJECT        ← latency / serving (separate)
SAFETY_PASS         | SAFETY_REJECT

QUALITY_PASS
  AND RUNTIME_PASS
  AND SAFETY_PASS
        ↓
CAMPAIGN_PROVISIONAL_OPEN
        ↓
%1 → %5 → %10 → controlled ramp

Any gate FAIL → Campaign Gate = CLOSED
```

Campaign requires **∧** (AND), not **∨** (OR).

---

## Current baseline (v3 — do not mis-attribute)

| Metric | v3 (CPU / Q4 path) | Bar |
|---|---|---|
| pos recall | ~0.51 | ≥ 0.90 |
| neg recall | ~0.81 | ≥ 0.95 |
| corr recall | ~0.60 | ≥ 0.90 |
| warm p50 | ~23 s | separate runtime gate |

**Interpretation:** 0.51 / 0.81 / 0.60 is a **real model/training gap**.  
Do **not** assign `QUANT` at this level. Required path: residual → targeted **v4** SFT → same-checkpoint quant matrix.

Near-miss example (illustrative): pos≈0.87, neg≈0.94, corr≈0.85 on CPU/Q4 → then *suspect* quant/CPU deploy, still confirmed only via §3 FP16/BF16 vs Qn on the same checkpoint + same dev set.

---

## Pipeline (locked order)

```text
V3 residual export (P17.1)
  → cause attribution
  → targeted v4 SFT
  → dev evaluation
  → same-checkpoint quant matrix
  → HR100  →  HR100_QUALITY_PASS | HR100_QUALITY_REJECT
  → fresh blind holdout (if HR100 errors mined into v4)
  → runtime gate  →  RUNTIME_PASS | RUNTIME_REJECT
  → safety gate
  → provisional campaign decision
```

---

## 0. Preconditions

- [ ] Freeze eval fixtures / HR100 annotations (no threshold edits to pass)
- [ ] Record exact artifacts: adapter path, GGUF path, base GGUF, llama-server flags, prompt template, `max_tokens`, temperature, threads
- [ ] Confirm eval port serves the checkpoint under test (`alias` / LoRA path logged)
- [ ] Campaign Gate unchanged for the duration of P17.x

---

## 1. Residual analysis (v3 first) — see also §A P17.1

Goal: classify each failure so v4 data targets the right hole.

### 1.1 Sets

| Split | Role |
|---|---|
| `train` | SFT only |
| `dev` | iteration / residual (not final blind) |
| `hr100` | final quality gate after `DEV_READY_FOR_HR100` |
| `blind` | unused for v4 mining; required if HR100 errors fed v4 |

> If v4 rows are mined from HR100 errors, HR100 is no longer a clean blind test. Run a **fresh blind holdout** before any quality claim.

### 1.2 Train / review leakage rule

**Wrong:** “HUMAN_REVIEWED rows must stay out of train.”  
**Right:** Rows used in **dev / hr100 / blind** must not appear in train (exact or near-duplicate). Independently authored HUMAN_REVIEWED train rows **are allowed**.

Recommended row fields:

```json
{
  "split": "train|dev|hr100|blind",
  "review_status": "HUMAN_REVIEWED",
  "source_fixture_id": null,
  "derived_from_eval_pattern": true
}
```

- `derived_from_eval_pattern=true` is OK for **pattern-inspired** synthetic/reviewed train rows.
- Must **not** be a verbatim or near-copy of an eval utterance.

### 1.3 Cause codes

| Cause code | Meaning |
|---|---|
| `MODEL_POS_MISS` | required positive missing |
| `MODEL_NEG_MISS` | required negative missing |
| `MODEL_CORR_MISS` | correction not reflected |
| `MODEL_OVER_EXTRACT` | extra positive/negative not licensed by utterance |
| `MODEL_EMPTY` | should extract something, got empty |
| `MODEL_CONFLICT` | same concept in positive **and** negative |
| `MODEL_FORBIDDEN_FIELD` | disallowed field or value emitted |
| `MODEL_HALLUCINATED_ENTITY` | bank / category / ID / entity not in utterance |
| `MATCHER` | NeedProfile correct; downstream match wrong |
| `QUANT_SUSPECT` | optional placeholder only in §1 — prefer leave blank |
| `QUANT` | **only after §3 proof** (ref OK, Qn wrong, same settings) |
| `RUNTIME_NON_JSON` | output is not JSON |
| `RUNTIME_SCHEMA_FAIL` | JSON present but schema-invalid |
| `RUNTIME_TRUNCATED` | output cut by token limit |
| `RUNTIME_TIMEOUT` | request timed out |

Do **not** bundle non-JSON under “model forbidden”. Without constrained decoding, non-JSON may be deployment/runtime.

### 1.4 QUANT attribution rule

During **v3 residual export**, `primary_cause = QUANT` is **forbidden** (no FP16/BF16 matrix yet).

- Prefer `primary_cause: null` until review; at most `QUANT_SUSPECT`.
- Assign `QUANT` only when:

```text
same checkpoint + same prompt + same dev set + same decoding
  AND ref (FP16/BF16) correct
  AND Q4 (or Qn) wrong
→ QUANT

ref and Q4 miss the same way
→ MODEL (or other non-quant cause)
```

### 1.5 Metrics (dev residual — recall alone is insufficient)

| Metric | Required for go/no-go to HR100 |
|---|---|
| pos recall / **pos precision** | report |
| neg recall / **neg precision** | report |
| corr recall | report |
| false positive rate / false negative rate | report |
| schema validity | → 1.00 before HR100 |
| forbidden count | → 0 before HR100 |
| over-extraction rate | report |
| empty-extraction accuracy | report |
| **positive_negative_conflict** | → **0** before HR100 |

Conflict example:

```text
User: Telefon istemiyorum.
Pred: positive=["telefon"], negative=["telefon"]  → conflict=1, FAIL
```

---

## 2. Targeted v4 training (only after P17.1 + §1)

- [ ] Build `need_profile_sft.v4.jsonl` from residual **pattern distribution** (controlled upsample — not one pattern × hundreds only)
- [ ] Enforce split leakage rule (§1.2)
- [ ] Train v4 (prefer GPU; CPU OK for scaffold only)
- [ ] Save `train_meta.json` + adapter + **one** FP16/BF16 reference export if possible
- [ ] **Do not** run HR100 yet

### 2.1 Dev gate before HR100

On **dev** only:

- [ ] schema validity = 1.00
- [ ] forbidden = 0
- [ ] positive_negative_conflict = 0
- [ ] pos/neg/corr recall + precision + FPR/FNR logged
- [ ] Decision: `DEV_READY_FOR_HR100` | `DEV_NEEDS_MORE_SFT` | `DEV_SUSPECT_DEPLOY`

---

## 3. Quantization matrix (same checkpoint)

**Hard rule:** identical adapter/checkpoint, identical prompt/decoding, identical **dev** set.

| Tier | Build | Serve | Dev metrics |
|---|---|---|---|
| Ref | FP16 or BF16 (highest fidelity) | same flags except quant | full §1.5 |
| Q6_K | from same adapter | same | full §1.5 |
| Q5_K_M | from same adapter | same | full §1.5 |
| Q4_K_M (current) | from same adapter | same | full §1.5 |

- [ ] Delta table: `metric(Qn) − metric(ref)`
- [ ] Attribute: ref strong + Q4 weak → `QUANT`; all weak → `MODEL`; decode/schema-only → `RUNTIME_*`

---

## 4. HR100 final quality gate (after DEV_READY)

Quality-only decision names:

| Result | Meaning |
|---|---|
| `HR100_QUALITY_PASS` | quality bars met |
| `HR100_QUALITY_REJECT` | quality bars failed |

| Check | Bar |
|---|---|
| pos recall | ≥ 0.90 |
| neg recall | ≥ 0.95 |
| corr recall | ≥ 0.90 |
| forbidden | = 0 |
| schema validity | = 1.00 |
| positive_negative_conflict | = 0 |
| unsafe auto-select | = 0 |

- [ ] Run HR100 only if `DEV_READY_FOR_HR100`
- [ ] If HR100 errors mined into v4 → run **blind holdout** before quality claim
- [ ] **No** Campaign open from HR100 alone

---

## 5. Runtime gate (separate)

Result names: `RUNTIME_PASS` | `RUNTIME_REJECT`.

v4 training will **not** turn ~23 s p50 into 2–3 s.

- [ ] Shrink system prompt; remove repeats
- [ ] Reduce context / max input
- [ ] `max_tokens` 384 → **96–128**
- [ ] reasoning/thinking off; temperature = 0
- [ ] grammar / JSON-schema constrained decoding
- [ ] warm model; thread / NUMA pin; batch tune
- [ ] drop unused schema fields

If 9B still too slow after quality-near:

| Option | Path |
|---|---|
| A | Stay on 9B — quality first, accept CPU latency work |
| B | **9B teacher → 3B/4B student** on narrow JSON task |

Quality PASS ∧ Runtime FAIL → Campaign **CLOSED**.

---

## 6. Matcher boundary (P17 does not fix matcher)

- NeedProfile correct + downstream wrong → `MATCHER`
- That row: **not** added to v4 SFT; **not** used to tank model metrics; sent to **matcher backlog**
- P17’s job is attribution hygiene, not matcher patches

---

## 7. Stop / go summary

| Quality | Runtime | Safety | Campaign |
|---|---|---|---|
| FAIL | * | * | CLOSED |
| * | FAIL | * | CLOSED |
| * | * | FAIL | CLOSED |
| PASS | PASS | PASS | **CAMPAIGN_PROVISIONAL_OPEN** → %1 → %5 → %10 |

---

## 8. Artifact log (fill during runs)

| Field | Value |
|---|---|
| Date / operator | |
| Checkpoint / adapter | |
| Quant tier | FP16/BF16 / Q6 / Q5 / Q4 |
| Dev set id + n | |
| Blind holdout id + n | |
| Metric dump path | |
| Primary attribution | MODEL_* / MATCHER / QUANT / RUNTIME_* |
| Next action | |

---

## Explicit non-goals

- Do not lower ADR-009 thresholds to greenwash
- Do not open Campaign on quality-only or latency-only
- Do not treat HR100-mined v4 as blind without a fresh holdout
- Do not assign `QUANT` during v3 residual without same-checkpoint ref evidence
- Do not change matcher code in P17

---

# A. P17.1 — V3 residual export (start here)

**Experiment id:** `P17-V3-RESIDUAL-001`  
**Runner:** `evaluation/p17_v3_residual_export.py` (nanobase → `:8026` LoRA-v3)  
**Artifacts:** `artifacts/p17/v3/{experiment_meta,residual_raw,residual_review,metrics,failure_patterns}`  
**Goal:** produce measurable residuals so v4 data plan comes from pattern distribution, not guesswork.

## A.1 Re-run and record raw outputs

For every dev (and optionally HR review) example, persist:

```json
{
  "experiment_id": "P17-V3-RESIDUAL-001",
  "utterance_id": "DEV-0042",
  "utterance": "Telefon değil tablet istiyorum",
  "gold": {
    "positive": ["tablet"],
    "negative": ["telefon"],
    "correction": true,
    "budget": null
  },
  "raw_response": "...",
  "parsed_response": {
    "positive": [],
    "negative": ["telefon"],
    "correction": false,
    "budget": null
  },
  "schema_valid": true,
  "truncated": false,
  "timeout": false,
  "latency_ms": 22140,
  "primary_cause": null,
  "secondary_causes": [],
  "review_notes": ""
}
```

**Raw response is mandatory.** Parsed-only storage cannot separate model error from parser error.

## A.2 Required artifacts

| Path | Contents |
|---|---|
| `artifacts/p17/v3/residual_raw.jsonl` | one runtime record per request (incl. raw) |
| `artifacts/p17/v3/residual_review.csv` | human review sheet |
| `artifacts/p17/v3/metrics.json` | aggregate metrics |
| `artifacts/p17/v3/failure_patterns.json` | cause counts + linguistic patterns |

### `residual_review.csv` columns

`utterance_id`, `utterance`, `gold_positive`, `pred_positive`, `gold_negative`, `pred_negative`, `gold_correction`, `pred_correction`, `schema_valid`, `forbidden`, `conflict`, `primary_cause`, `secondary_causes`, `review_notes`

### `metrics.json` shape

```json
{
  "pos_recall": 0.0,
  "pos_precision": 0.0,
  "neg_recall": 0.0,
  "neg_precision": 0.0,
  "corr_recall": 0.0,
  "false_positive_rate": 0.0,
  "false_negative_rate": 0.0,
  "schema_validity": 0.0,
  "forbidden_count": 0,
  "conflict_count": 0,
  "over_extraction_rate": 0.0,
  "empty_extraction_accuracy": 0.0
}
```

### `failure_patterns.json` shape

```json
{
  "MODEL_POS_MISS": { "count": 0, "patterns": [] },
  "MODEL_NEG_MISS": { "count": 0, "patterns": [] },
  "MODEL_CORR_MISS": { "count": 0, "patterns": [] }
}
```

## A.3 Cause coding order (primary / secondary)

Multiple failures allowed → `primary_cause` + `secondary_causes[]`.

Decide **primary** in this order:

1. Timeout / truncate / non-JSON / schema → `RUNTIME_*`
2. Forbidden field or hallucinated entity → `MODEL_FORBIDDEN_FIELD` / `MODEL_HALLUCINATED_ENTITY`
3. Same concept in pos and neg → `MODEL_CONFLICT`
4. Expected extraction fully empty → `MODEL_EMPTY`
5. Correction wrong → `MODEL_CORR_MISS`
6. Positive missing → `MODEL_POS_MISS`
7. Negative missing → `MODEL_NEG_MISS`
8. Extra entity → `MODEL_OVER_EXTRACT`
9. NeedProfile correct, downstream wrong → `MATCHER`

Example:

```text
User: Telefon değil tablet istiyorum.
Gold: pos=[tablet], neg=[telefon], correction=true
Pred: pos=[], neg=[], correction=false
```

```json
{
  "primary_cause": "MODEL_CORR_MISS",
  "secondary_causes": ["MODEL_POS_MISS", "MODEL_NEG_MISS", "MODEL_EMPTY"]
}
```

Correction is the main semantic failure → primary.

`MATCHER` rows → matcher backlog only (not v4 SFT, not model-metric penalty).

During P17.1: **do not** set `primary_cause = QUANT`.

## A.4 Linguistic failure patterns (for v4 mix)

Cause counts alone are not enough. Tag patterns such as:

| Pattern id | Example |
|---|---|
| `NEG_SIMPLE` | “Telefon istemiyorum.” |
| `CORRECTION_X_NOT_Y` | “Telefon değil tablet istiyorum.” |
| `CORRECTION_RETRACTION` | “Tabletten vazgeçtim, laptop bakıyorum.” |
| `NEGATION_OF_NEGATION` | “Telefon istemiyorum demedim.” |
| `MULTI_POS_SINGLE_NEG` | “Laptop veya tablet olabilir ama telefon olmasın.” |
| `BUDGET_PLUS_CORRECTION` | “30 bine telefon değil laptop bakıyorum.” |
| `SLANG` | “Telefon sarmıyor, laptop bakıyorum.” |

v4 data follows this **distribution** with controlled mix — not only the single worst pattern inflated.

## A.5 P17.1 done criteria

- [ ] Every dev row has **raw** output
- [ ] Every failing row has `primary_cause`
- [ ] `secondary_causes` recorded where applicable
- [ ] MODEL / MATCHER / RUNTIME split done
- [ ] `QUANT` not assigned without §3 evidence
- [ ] Top failure pattern list produced
- [ ] Target v4 example counts per pattern set
- [ ] HR100 / blind / dev rows not mixed into train
- [ ] Campaign Gate unchanged

---

**Start now:** produce v3 **dev** residual export, code primary/secondary causes, derive v4 data plan from that distribution.

# Operator runbook — ADR-013 layered verification

**Goal:** Run Taksitlio Query Golden Set v1 and interpret layered gates.
Parser lane runs on Mac/CI with TEST fixtures. Staging/live product and
finance lanes run on **nanobase** (server-only ops rule).

## Artifacts

| Path | Role |
|------|------|
| [`docs/adr/ADR-013-layered-verification-and-release-gates.md`](../adr/ADR-013-layered-verification-and-release-gates.md) | Binding decisions |
| `evaluation/schemas/query_golden_case.schema.json` | Case schema |
| `evaluation/datasets/query_golden/v1/query_golden.v1.jsonl` | 1000 cases |
| `evaluation/datasets/query_golden/v1/manifest.json` | Version + bucket counts |
| `evaluation/config/query_golden_gates.v1.json` | Gate thresholds |
| `evaluation/_run_query_golden_v1.py` | Lane runner |
| `src/taksitlio/evaluation/query_golden/` | Loader + metrics |

## Environments

| Env | Data | Who |
|-----|------|-----|
| TEST | Controlled catalog fixture | CI / local unit |
| STAGING | Real merchants/products/banks | Test users on server |
| PRODUCTION | Gate-passed merchants only | End users |

Do **not** bind demo/fixture products into staging or production chatbot UI.

## Regenerate dataset (optional)

```bash
export PYTHONPATH=src
python evaluation/datasets/_generate_query_golden_v1.py
```

Writes `evaluation/datasets/query_golden/v1/query_golden.v1.jsonl` + updates
manifest counts. After a version is **locked** in `manifest.json`
(`immutable: true`), do not overwrite; cut `v2` instead.

## Run parser lane (TEST)

```bash
export PYTHONPATH=src
python evaluation/_run_query_golden_v1.py --lane parser
```

Optional:

```bash
python evaluation/_run_query_golden_v1.py --lane parser \
  --dataset evaluation/datasets/query_golden/v1/query_golden.v1.jsonl \
  --gates evaluation/config/query_golden_gates.v1.json \
  --out evaluation/reports/query-golden-v1-parser.json
```

Report includes precision/recall metrics, `false_auto_resolution_count`,
LLM routing violations, and `gate.status` (`PASS` / `FAIL` / `BOOTSTRAP`).

**BOOTSTRAP:** DRAFT-heavy sets cannot full-ACCEPT. Parser infrastructure
PASS requires schema-valid 1000 + runner green; promotion thresholds in
`query_golden_gates.v1.json` apply when enough `HUMAN_REVIEWED` cases exist
(`minimum_human_reviewed` in gates config) and `DRAFT=0`.

## Retrieval + finance lanes (TEST fixtures)

```bash
python evaluation/_run_query_golden_v1.py --lane retrieval
python evaluation/_run_query_golden_v1.py --lane finance
python evaluation/_run_query_golden_v1.py --lane e2e
```

- **retrieval** — same Query Golden utterances + TEST product pool
  (`evaluation/fixtures/query_golden_test_products.json`). Zero-tolerance:
  budget / merchant / negation / RAM filter leaks.
- **finance** — `finance_scenarios.v1.jsonl` eligibility + payment golden.
  Zero-tolerance: expired/no-agreement shown, wrong monthly/total.
- **e2e** — composes parser + retrieval + finance on TEST only.

Staging real-merchant bind remains open (nanobase); do not treat TEST PASS
as Real Product Data Gate.

## Gate checklist (release)

Zero-tolerance before general open:

- [ ] Parser Gate
- [ ] Entity Resolution Gate (incl. data-driven fuzzy; no static typo map)
- [ ] Clarification Gate
- [ ] Real Product Data Gate
- [ ] Image Quality Gate
- [ ] Finance Mapping Gate
- [ ] Payment Calculation Gate
- [ ] Recommendation Integrity Gate
- [ ] Progress Truthfulness Gate
- [ ] Performance Gate
- [ ] Shadow Mode Gate (≥1000 anonymous queries)
- [ ] UAT Gate (business + catalog + end-user)

## Data-driven fuzzy proof

```bash
pytest tests/acceptance/query_golden/test_data_driven_fuzzy.py -q
```

Adds a merchant only in an in-memory catalog (not in shipped source maps),
queries a typo form, expects resolve without code deploy.

## Staging notes (server)

On nanobase, after real catalog bind:

1. Point retrieval/finance lanes at staging DB (runbook extensions TBD).
2. Never run crawlers/feeds on the Mac checkout.
3. Shadow mode: dual-run new path; log category/merchant/bank/product/price/term/LLM diffs.

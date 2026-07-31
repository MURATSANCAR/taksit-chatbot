# TODO — post ADR-007 / ADR-008 / ADR-009

Status date: 2026-07-31

## Closed — ADR-007 safety

- [x] FAST pos/neg/correction extraction + validator
- [x] Oracle vs E2E lanes; forbidden=0, unsafe=0
- [x] ≥100 HUMAN_REVIEWED; decision_policy_error ↓
- [x] Substring alias guardrail; NON_PURCHASE intent

## Closed — ADR-008 P0 quality retrieval

- [x] Morphology-safe concept variants (surface preserved)
- [x] Token-set alias retrieval (no substring)
- [x] E2E retrieval diagnostics + failure stage codes
- [x] Sibling-alias conflict soft-exclude
- [x] V013 morphology-safe retrieval policy migration
- [x] validation/dev v4 datasets
- [x] Gate statuses: QUALITY_READY_RUNTIME_BLOCKED / QUALITY_REJECT

## Closed — ADR-008 P0.1 residual closeout

- [x] Soft-exclude expansion + concept coverage + diversify Top-K
- [x] V014 top-K diversification policy migration
- [x] E2E top_1 ≥ 0.65 / top_2 ≥ 0.90 / required ≥ 0.88

## In progress — ADR-008 P1 / ADR-009 runtime verification

- [x] ADR-009 accepted (real runtime ≠ test-double)
- [x] Runtime dependency probes + provisional / campaign gates
- [x] Redis integration suite under `tests/integration/redis` (CI skip=0)
- [x] pgvector integration suite under `tests/integration/pgvector` (CI skip=0)
- [x] `RemoteFastExtractor` + `StrictOpenAICompatibleEmbedder` (no silent fallback)
- [x] Bootstrap templates: `poc-fast-understanding.sql`, `poc-category-embedding.sql`
- [x] `docker/docker-compose.runtime.yml` (redis + pgvector/pg16 profile)
- [x] `.github/workflows/runtime-verification.yml`
- [ ] Live FAST deployment health + Turkish extraction eval (env-configured)
- [ ] Live CATEGORY_EMBEDDING rebuild + quality comparison
- [ ] pgvector 100 / 1k / 10k benchmarks measured
- [ ] Full Redis+FAST+embedding+pgvector E2E latency stages
- [ ] `PROVISIONAL_ACCEPT` after all `real_*_measured=true`
- [ ] Campaign Gate `READY_TO_OPEN` (no campaign code in this sprint)

## Gates

| Gate | Status |
|---|---|
| Safety | PASS (test-double baseline) |
| Oracle Quality | PASS (baseline retained) |
| E2E Quality | PASS (baseline retained) |
| Runtime Dependency | BLOCKED_DEPENDENCY until live FAST+embedding+Redis+pgvector measured |
| Provisional | deferred — see `evaluation/reports/adr008-p1-gate.json` |
| Campaign | CLOSED (opens only after PROVISIONAL_ACCEPT) |

# TODO — post ADR-007 / ADR-008

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

## Open — P0 residual (quality)

- [ ] E2E top_1 ≥ 0.65 (now 0.649 — one case)
- [ ] E2E top_2 ≥ 0.90 (now 0.882)
- [ ] E2E required_recall ≥ 0.88 (now 0.860)

## Open — P1 runtime

- [ ] Real FAST deployment + measured latency / recall
- [ ] Real CATEGORY_EMBEDDING (else BLOCKED_DEPENDENCY)
- [ ] pgvector integration skip=0 + scale benchmarks
- [ ] Redis integration skip=0 in CI/local RC
- [ ] PROVISIONAL_ACCEPT after runtime green

## Gates

| Gate | Status |
|---|---|
| Safety | PASS |
| Oracle Quality | PASS (near/above provisional bar) |
| E2E Quality | REJECT (top_1/top_2/req slightly under) |
| Runtime Dependency | BLOCKED_DEPENDENCY |
| Campaign | CLOSED |
| Sprint gate | QUALITY_REJECT (E2E) — not PROVISIONAL_ACCEPT |

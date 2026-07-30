# TODO — post ADR-007 hardening

Status date: 2026-07-31

## Closed (ADR-007 safety)

- [x] FAST positive / negative / correction extraction
- [x] Negation, correction, indecision cues; bilateral + trailing-değil
- [x] Bare negative head-noun; stopword-only concept block
- [x] Category ID / fixture key forbidden
- [x] SemanticConstraintValidator
- [x] Oracle vs E2E evaluation lanes
- [x] forbidden=0, unsafe=0 (oracle + E2E)
- [x] ≥100 HUMAN_REVIEWED validation cases
- [x] Decision policy error reduction (~0.23 → ~0.09)
- [x] Substring alias guardrail held; NON_PURCHASE intent held
- [x] Regression tests for OOS / unsafe utterances

## Open — P0 (ADR-008 quality)

- [ ] Morphology-safe concept variants (surface preserved; strip as alternate)
- [ ] Token-set alias retrieval (no substring)
- [ ] E2E retrieval diagnostics + failure stage codes
- [ ] E2E: status≥0.78, top_1≥0.65, top_2≥0.90, required≥0.88

## Open — P1

- [ ] Oracle top_1 ≥ 0.65 without lowering threshold
- [ ] Real FAST deployment + measured latency / recall
- [ ] Real CATEGORY_EMBEDDING (else BLOCKED_DEPENDENCY)
- [ ] pgvector integration skip=0 + scale benchmarks
- [ ] Redis integration skip=0 in CI/local release candidate

## Gates

| Gate | Status |
|---|---|
| Safety | PASS |
| Oracle Quality | NEAR_PASS |
| E2E Quality | REJECT |
| Runtime Dependency | BLOCKED_DEPENDENCY |
| Campaign | CLOSED |

Campaign layer opens only after PROVISIONAL_ACCEPT (see ADR-008).

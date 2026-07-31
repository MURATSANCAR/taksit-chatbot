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

## Closed — ADR-008 P0.1 residual closeout

- [x] Soft-exclude expansion (strong + soft + catalog-compatible)
- [x] `ConceptCoverageScorer` bonus (semantic_description / use_case fallback)
- [x] `diversify_top_k` + parent-demotion + sibling-diversity
- [x] `force_parent_child_collapse_on_direct_alias`
- [x] Policy fields + `__post_init__` range validation
- [x] V014 top-K diversification policy migration
- [x] `RetrievalDiagnostic` reason codes (RANKED_3, SIBLING_MISSING, PARENT_CROWDS_OUT_CHILD, ...)
- [x] Residual analysis report (`evaluation/reports/adr008-p01-residual-analysis.json`)
- [x] E2E top_1 ≥ 0.65 (now 0.684)
- [x] E2E top_2 ≥ 0.90 (now 0.904)
- [x] E2E required_recall ≥ 0.88 (now 0.880)

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
| Oracle Quality | PASS (top_2=1.00, required=1.00) |
| E2E Quality | PASS (top_1=0.684, top_2=0.904, required=0.880, status=0.842) |
| Runtime Dependency | BLOCKED_DEPENDENCY |
| Campaign | CLOSED |
| Sprint gate | QUALITY_READY_RUNTIME_BLOCKED — not PROVISIONAL_ACCEPT |

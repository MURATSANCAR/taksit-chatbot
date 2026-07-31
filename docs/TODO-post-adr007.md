# TODO — post ADR-007 / ADR-008 / ADR-009

Status date: 2026-07-31

## Closed — ADR-007 safety

- [x] FAST pos/neg/correction extraction + validator
- [x] Oracle vs E2E lanes; forbidden=0, unsafe=0
- [x] ≥100 HUMAN_REVIEWED; decision_policy_error ↓
- [x] Substring alias guardrail; NON_PURCHASE intent

## Closed — ADR-008 P0 / P0.1 quality

- [x] Morphology-safe + token-set + residual closeout (top_2/required bar)
- [x] V013 / V014 policies; QUALITY_READY_RUNTIME_BLOCKED

## Closed — ADR-009 kod iskeleti

- [x] Runtime probes + provisional / campaign gates
- [x] Redis / pgvector integration suites (CI skip=0)
- [x] RemoteFastExtractor + StrictOpenAICompatibleEmbedder
- [x] Bootstrap SQL + compose.runtime + runtime-verification workflow
- [x] Live runbook: [`docs/runbooks/ADR-009-live-runtime-verification.md`](runbooks/ADR-009-live-runtime-verification.md)
- [x] `python -m taksitlio.db.migrate` + `.env.runtime` gitignore

## Open — canlı sunucu (runbook; kod değişikliği yok)

Operatör runbook’u çalıştırır. Matcher / threshold / dataset **dokunulmaz**.

- [ ] Sunucuda Docker + Redis + pgvector ayakta
- [ ] Live FAST health + Türkçe extraction eval
- [ ] Live CATEGORY_EMBEDDING rebuild + quality comparison
- [ ] pgvector 100 / 1k / 10k benchmark
- [ ] Full E2E stage latency
- [ ] `PROVISIONAL_ACCEPT` (`real_*_measured=true`)
- [ ] Campaign Gate `READY_TO_OPEN` → sonra kampanya domain tasarımı

## Gates

| Gate | Status |
|---|---|
| Safety | PASS (baseline) |
| Quality | QUALITY_READY (baseline) |
| Runtime | BLOCKED_DEPENDENCY → runbook |
| Provisional | BLOCKED_DEPENDENCY → runbook |
| Campaign | CLOSED |

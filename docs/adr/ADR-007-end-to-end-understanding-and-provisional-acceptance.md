# ADR-007: End-to-End Understanding and Provisional Acceptance

## Durum

**Hardening tamamlandı (2026-07-31).** Safety Gate = PASS.
Quality Gate = REJECT. Runtime Dependency = BLOCKED_DEPENDENCY.
**Kampanya katmanı kapalı.**

Sonraki çalışma: morphology-safe retrieval + gerçek runtime doğrulama
(bkz. [ADR-008](ADR-008-morphology-safe-retrieval-and-runtime-verification.md)).

## Bağlam

Matcher oracle input’ta `candidate_recall_at_pool=1.00` iken status/decision
darboğazı vardı. Annotated `semantic_constraints` runtime FAST çıktısı değildir;
iki hat zorunlu. Out-of-scope düğümler `matchable=false` ile retrieve edilebilir
ama asla MATCHED seçilemez.

## Karar

1. İki evaluation hattı: `MATCHER_ORACLE_INPUT` vs `END_TO_END_RUNTIME_INPUT`.
2. Oracle annotated constraints kullanır; runtime yalnızca utterance → FAST → NeedProfile.
3. FAST kategori ID / fixture key / katalog kodu üretemez; natural-language concept.
4. `SemanticConstraintValidator` model çıktısını matcher’a vermeden doğrular.
5. Unsafe auto-select count ve forbidden violation count = 0 kritik (sağlandı).
6. PROVISIONAL_ACCEPT için ≥100 HUMAN_REVIEWED + E2E kalite + gerçek runtime ölçümü.
7. Final ACCEPT için bağımsız holdout + daha geniş review.
8. LexicalEmbedder production latency sayılmaz; gerçek embedding yoksa
   `BLOCKED_DEPENDENCY`.
9. Non-matchable / OUT_OF_SCOPE düğümler final Top-K’ye girmez ve AUTO_SELECT olmaz.
10. ChatOrchestrator: FAST → validate → CAS → matcher → CategoryResolutionApplier CAS;
    blind retry yok, max bir re-evaluation.
11. Substring alias matching geri getirilmez; NON_PURCHASE query-intent korunur.
12. Kalite eşiğini geçmek için threshold düşürülmez — case-level ranking düzeltilir.

## Hardening kapanış metrikleri (validation v3)

| Lane | forbidden | unsafe | status | top_1 | top_2 | req | pool | decision_err |
|---|---|---|---|---|---|---|---|---|
| Oracle | **0** | **0** | 0.855 | 0.649 | 0.922 | 0.895 | 1.00 | 0.092 |
| E2E | **0** | **0** | 0.766 | 0.439 | 0.732 | 0.658 | 1.00 | 0.072 |

- HUMAN_REVIEWED = 100; NO_MATCH support = 116
- Tests: 186 passed; Redis integration 2 skipped (CI’da skip=0 zorunlu)
- Oracle Top-1: 0.649 vs 0.650 — threshold düşürülmez; 1–N ranking case düzeltilir

## Gate özeti

| Gate | Durum |
|---|---|
| Safety | PASS |
| Oracle Quality | NEAR_PASS |
| E2E Quality | REJECT |
| Runtime Dependency | BLOCKED_DEPENDENCY |
| Campaign | CLOSED |

## Reddedilen alternatifler

* Annotated constraint’leri runtime geçmiş gibi saymak
* Auto-select eşiğini körlemesine düşürmek
* N=5 no-match ile unsafe rate’e güvenmek
* FAST’ten kategori seçtirmek
* pgvector’ü sürekli skip bırakmak
* DRAFT ile final ACCEPT
* Kalite kapısını geçmek için provisional threshold düşürmek
* Substring alias matching’i geri getirmek

## Sonuçlar

**Olumlu:** Forbidden/unsafe = 0 (oracle + E2E); negation/correction runtime’a aktı;
decision_policy_error ~0.23 → ~0.09; dual lane; OOS/matchable güvenliği.

**Açık borç:** E2E concept over-normalization (`masaüstü`→`masaüst`); oracle top_1
0.001 fark; gerçek FAST/embedding/pgvector/Redis integration-green.

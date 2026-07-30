# ADR-007: End-to-End Understanding and Provisional Acceptance

## Durum

Kabul edildi — kampanya katmanı kapalı

## Bağlam

Matcher oracle input’ta `candidate_recall_at_pool=1.00` iken `status_accuracy≈0.73`
ve `decision_policy_error_rate≈0.20` — darboğaz ranking/decision. Annotated
`semantic_constraints` runtime FAST çıktısı değildir; iki hat zorunlu.

Out-of-scope katalog düğümleri `matchable=false` ile işaretlenir; retrieve
edilebilir ama asla MATCHED seçilemez (forbidden/unsafe root-cause).

## Karar

1. İki evaluation hattı: `MATCHER_ORACLE_INPUT` vs `END_TO_END_RUNTIME_INPUT`.
2. Oracle annotated constraints kullanır; runtime yalnızca utterance → FAST → NeedProfile.
3. FAST kategori ID / fixture key / katalog kodu üretemez; natural-language concept.
4. `SemanticConstraintValidator` model çıktısını matcher’a vermeden doğrular.
5. Unsafe auto-select count ve forbidden violation count = 0 kritik.
6. PROVISIONAL_ACCEPT için ≥100 HUMAN_REVIEWED validation case.
7. Final ACCEPT için bağımsız holdout + daha geniş review (bu sprintte yok).
8. LexicalEmbedder production latency sayılmaz; gerçek embedding yoksa
   `BLOCKED_DEPENDENCY` / `EMBEDDING_DEPLOYMENT_UNAVAILABLE`.
9. Non-matchable / OUT_OF_SCOPE düğümler final Top-K’ye girmez ve AUTO_SELECT olmaz.
10. ChatOrchestrator: FAST → validate → CAS → matcher → CategoryResolutionApplier CAS;
    blind retry yok, max bir re-evaluation.

## Reddedilen alternatifler

* Annotated constraint’leri runtime geçmiş gibi saymak
* Auto-select eşiğini körlemesine düşürmek
* N=5 no-match ile unsafe rate’e güvenmek
* FAST’ten kategori seçtirmek
* pgvector’ü sürekli skip bırakmak
* DRAFT ile final ACCEPT

## Sonuçlar

**Olumlu:** İzole matcher vs E2E ayrımı; OOS güvenliği; provisional gate yolu.

**Risk:** HUMAN_REVIEWED üretimi süreç gerektirir; gerçek FAST/embedding CI’da
BLOCKED_DEPENDENCY olabilir.

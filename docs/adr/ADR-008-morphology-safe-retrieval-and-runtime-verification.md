# ADR-008: Morphology-Safe Retrieval and Runtime Verification

## Durum

Önerildi — ADR-007 hardening kapanışından sonraki sprint.
Kampanya katmanı bu ADR kabul edilip PROVISIONAL_ACCEPT alınana kadar kapalı.

## Bağlam

ADR-007 güvenlik hedefleri tamamlandı (`forbidden=0`, `unsafe=0`,
`fast_extraction_failures=0`). Kalite kapısı REJECT: E2E’de FAST concept
over-normalization exact-alias sinyalini düşürüyor; oracle top_1 0.649≈0.650;
gerçek FAST/embedding/pgvector/Redis henüz ölçülmedi.

## Karar (sıradaki iş)

### P0 — Quality retrieval

1. **Morphology-safe concept variants** — surface form korunur; stripped form
   yalnızca alternatif; alias tablosunda surface varsa agresif strip yok;
   kategori-özel hardcode yok; `normalization_source` provenance.
2. **Token-set alias** — substring eşleşme yok; exact phrase / token-set /
   prefix-safe / n-gram / vector ayrı sinyaller; negatif constraint token-set’te de.
3. **E2E retrieval diagnostics** — utterance → surface → normalized → variants →
   channels → pool → rank → decision; reason codes:
   `SURFACE_FORM_LOST`, `OVER_NORMALIZED_CONCEPT`, `ALIAS_VARIANT_MISSING`,
   `TOKEN_SET_MISS`, `CORRECT_CANDIDATE_RANKED_LOW`.
4. **E2E quality targets** (kampanya öncesi minimum):
   status ≥ 0.78, top_1 ≥ 0.65, top_2 ≥ 0.90, required ≥ 0.88,
   forbidden=0, unsafe=0, invalid_schema=0.

### P1 — Oracle + runtime

5. Oracle top_1 case-level ranking (threshold düşürmeden ≥ 0.65).
6. Gerçek FAST deployment + latency / invalid JSON / negation recall.
7. Gerçek CATEGORY_EMBEDDING deployment; yoksa `BLOCKED_DEPENDENCY`.
8. pgvector canlı integration (skip=0) + 100/1k/10k benchmark.
9. Redis integration skip=0; typed failure; no in-memory production fallback.

### PROVISIONAL_ACCEPT

HUMAN_REVIEWED ≥ 100; oracle top_1 ≥ 0.65; E2E status/top_1/top_2/required
yukarıdaki P0 hedefleri; forbidden=unsafe=0; Redis/pgvector skip=0;
real FAST + real embedding measured=true. Final ACCEPT ayrı holdout ister.

## Reddedilen

* Threshold düşürerek gate geçmek
* Substring alias geri getirmek
* Kategori-özel hardcode ranking kuralı
* Lexical fallback’i production latency gibi raporlamak
* Kampanyayı E2E kalite/REJECT iken açmak

## Çalışma sırası

1. Morphology-safe concept variants
2. Token-set alias retrieval
3. E2E retrieval diagnostics
4. E2E Top-1 / Top-2 / required recall
5. Oracle Top-1 case-level
6. Redis integration skip kaldırma
7. Gerçek FAST
8. Gerçek CATEGORY_EMBEDDING
9. pgvector benchmark
10. PROVISIONAL_ACCEPT
11. Kampanya retrieval katmanı

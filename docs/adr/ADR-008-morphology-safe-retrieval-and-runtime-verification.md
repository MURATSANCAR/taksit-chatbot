# ADR-008: Morphology-Safe Retrieval and Runtime Verification

## Durum

**P0 Quality Retrieval — uygulandı (2026-07-31).**
Safety Gate = PASS. E2E quality iyileşti; provisional E2E bar henüz tam
geçmedi (`top_1=0.649`, `top_2=0.882`, `req=0.860`). Runtime = BLOCKED_DEPENDENCY.
Kampanya katmanı kapalı. Gate: `QUALITY_REJECT` (E2E) / oracle quality-ready
ama runtime blocked.

## Bağlam

ADR-007 hardening sonrası E2E top_1≈0.44; kök neden surface form over-strip
(`masaüstü`→`masaüst`). Substring alias yasak; token-set + morphology-safe
variant modeli gerekli.

## P0 kararları (uygulandı)

1. Surface form her zaman primary; morphological strip yalnız variant.
2. `TurkishMorphologySafeNormalizer` + constraint `surface_form` / `variants` /
   `normalization_source` (schema + validator geriye uyumlu).
3. `TokenSetAliasRetriever` — exact phrase / token-set / prefix-safe / n-gram /
   morph; **substring yok**.
4. Negative hard-exclude: surface / normalized / token-set / token-membership;
   n-gram ve zayıf morph hard-exclude değil.
5. Sibling-alias conflict (kulaklık/hoparlör→audio): hard-exclude yerine soft
   penalty; positive strong hit varsa kategori kalır.
6. `RetrievalDiagnostic` + reason codes; utterance standart raporda yok.
7. Policy alanları V013 migration ile.
8. Validation/dev **v4** dataset (v3 + morphology/token-set regression DRAFT).
9. Provisional profile P0’da `PROVISIONAL_ACCEPT` vermez →
   `QUALITY_READY_RUNTIME_BLOCKED` veya `QUALITY_REJECT`.

## P0 metrikleri (validation v3 challenger, LexicalFallback)

| Lane | forbidden | unsafe | status | top_1 | top_2 | req | pool |
|---|---|---|---|---|---|---|---|
| Oracle baseline | 0 | 0 | 0.855 | 0.649 | 0.922 | 0.895 | 1.00 |
| Oracle ADR-008 | **0** | **0** | **0.952** | **0.895** | **1.00** | **1.00** | **1.00** |
| E2E baseline | 0 | 0 | 0.766 | 0.439 | 0.732 | 0.658 | 1.00 |
| E2E ADR-008 | **0** | **0** | **0.862** | **0.649** | **0.882** | **0.860** | **1.00** |

E2E top_1: 0.439 → 0.649 (+0.21). Hedef 0.65’e 0.001 kaldı — threshold
düşürülmedi. top_2 / required hâlâ ≤ hedeflerin biraz altında.

## P1 (açık)

Gerçek FAST, CATEGORY_EMBEDDING, pgvector skip=0, Redis skip=0,
ardından PROVISIONAL_ACCEPT.

## Reddedilen

* Threshold düşürmek · substring alias · kategori hardcode · kampanya açmak ·
  LexicalFallback’i production latency saymak

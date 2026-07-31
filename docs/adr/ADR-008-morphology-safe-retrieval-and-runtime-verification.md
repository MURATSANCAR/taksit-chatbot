# ADR-008: Morphology-Safe Retrieval and Runtime Verification

## Durum

**P0.1 Residual Closeout — uygulandı (2026-07-31).**
Safety Gate = PASS. E2E provisional bar geçildi
(`top_1=0.684`, `top_2=0.904`, `req=0.880`, `status=0.842`, `forbidden=0`,
`unsafe=0`, `pool=1.00`). Runtime = BLOCKED_DEPENDENCY. Kampanya katmanı
kapalı. Gate: `QUALITY_READY_RUNTIME_BLOCKED` (E2E + oracle) — P1 runtime
ölçümüne kadar `PROVISIONAL_ACCEPT` verilmez.

**P0 Quality Retrieval — uygulandı (2026-07-31).**
Safety Gate = PASS; E2E `top_1=0.649`, `top_2=0.882`, `req=0.860` — hedefin
biraz altında (P0.1 ile kapatıldı).

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

## P0.1 kararları (uygulandı — 2026-07-31)

Kalıntı ~8 miss: sibling/shared-category negative (`süpürge`/`saat`/
`kulaklık`/`iphone`) hedef kategoriyi HARD-EXCLUDE ediyordu çünkü
positive (`robot`/`bileklik`/`kablolu`/`android`) zayıftı. P0.1 ranking
katmanına dokunur; safety gating ve auto-select V013 aynı kalır.

1. **Soft-exclude expansion (matcher.py).** `pos_alias_hit` üç kanalın
   birleşimi: strong (surface/normalized/token_set ≥ 0.9), soft
   (prefix_safe ≥ 0.8 · morphological_variant > 0 · character_ngram ≥
   `character_ngram_min_similarity`) ve catalog-text compatibility
   (`_positive_catalog_compatible` — token-boundary, no substring, no
   hardcode). `neg_alias_hit` + herhangi biri → `conflict_same_node`:
   hard-exclude yerine `explicit_negative_penalty *
   sibling_soft_exclusion_factor` (0.20) uygulanır. Yalnız strong
   surface positive `direct_alias_match`’i korur.
2. **Concept coverage bonus.** `ConceptCoverageScorer(coverage_weight=
   policy.concept_coverage_weight)` FAST positif kavramlarının hangi
   node üzerinde token-boundary kapsandığını puanlar; alias miss
   kaldığında `semantic_description`/`use_case` üzerindeki whole-token
   overlap küçük bir soft credit üretir. Auto-select değildir; kapak
   +0.20 puandır.
3. **Diversify Top-K.** `collapse_parent_child` çıkışı
   `diversify_top_k(index=..., policy=...)` içine bağlanır; slot
   fill positive-channel’a öncelik verir, sibling diversity aynı ebeveynin
   çocuklarını üst üste yığmaz. `parent_demoted:*` /
   `sibling_diverse:*` / `signal_prefer:*` notları diagnostics’e girer.
4. **Force parent demotion.** `same_parent_penalty=0.06` çalışan
   `_demote_crowding_parents` bir parent rank 1 iken viable child
   varsa (score ≥ `minimum_candidate_score * 0.5`) parent’ı yumuşak
   demote eder. `force_parent_child_collapse_on_direct_alias=True`
   parent’ın direct-alias hit’i varsa mevcut gap’ten bağımsız collapse
   eder.
5. **Policy fields (challenger defaults).** `sibling_soft_exclusion_factor=
   0.20`, `concept_coverage_weight=0.10`, `diversification_enabled=True`,
   `same_parent_penalty=0.06`, `prefer_positive_channel_in_topk=True`,
   `sibling_diversity_enabled=True`,
   `force_parent_child_collapse_on_direct_alias=True`,
   `character_ngram_weight=0.50` (ranking-only; auto-select V013 kapısı
   değişmedi). Aralık doğrulaması `__post_init__` içinde.
6. **Migration V014.** `db/migrations/V014__topk_diversification_policy.sql`
   ADR-008 P0.1 alanlarını additive olarak ekler, `CATEGORY_MATCH_DEFAULT`
   `configuration` blob’unu senkronlar. Auto-select ayarlarına dokunmaz.
7. **Diagnostics.** `RetrievalDiagnostic` `diversity_notes`,
   `hierarchy_relations`, `concept_coverage` ile genişletildi;
   `CORRECT_CANDIDATE_RANKED_3`, `REQUIRED_SIBLING_MISSING`,
   `NEGATIVE_PENALTY_TOO_STRONG`, `PARENT_CROWDS_OUT_CHILD` reason
   kodları utterance içermeden expected fixture keys’den türetiliyor.
8. **Residual raporu.** `evaluation/_run_adr008_p01.py` v4 Oracle + E2E
   koşularını üretir; kova bazlı özet ve case_id listesi
   `evaluation/reports/adr008-p01-residual-analysis.json`.

## P0.1 metrikleri (validation v4, LexicalFallback)

| Lane | forbidden | unsafe | status | top_1 | top_2 | req | pool |
|---|---|---|---|---|---|---|---|
| Oracle P0 | 0 | 0 | 0.952 | 0.914 | 1.000 | 1.000 | 1.00 |
| Oracle **P0.1** | **0** | **0** | **0.952** | **0.914** | **1.000** | **1.000** | **1.00** |
| E2E P0 | 0 | 0 | 0.862 | 0.649 | 0.882 | 0.860 | 1.00 |
| E2E **P0.1** | **0** | **0** | **0.842** | **0.684** | **0.904** | **0.880** | **1.00** |

E2E provisional bar (top_1 ≥ 0.65, top_2 ≥ 0.90, required ≥ 0.88, status
≥ 0.78, forbidden = 0, unsafe = 0, pool = 1.00) tamamı geçildi. Threshold
düşürülmedi; safety Oracle’da regresyon yok. Gate:
`QUALITY_READY_RUNTIME_BLOCKED` (P1 runtime ölçümüne kadar
`PROVISIONAL_ACCEPT` verilmez).

Not: E2E `status_accuracy` P0 → P0.1 arasında 0.862 → 0.842 (−0.020)
kaydı verdi (daha fazla kategori artık kurtarıldığı için AMBIGUOUS
kararı biraz genişledi); bar 0.78 olduğundan gate’i etkilemez. `decision_
policy_error_rate` E2E: 0.0952. Kalıntı analiz: `evaluation/reports/
adr008-p01-residual-analysis.json` (Oracle 9 kayıt, E2E 26 kayıt).

## P1 / ADR-009 (runtime verification — scaffolding landed 2026-07-31)

Matcher heuristics **locked**. New work lives under ADR-009:

* [`docs/adr/ADR-009-real-runtime-verification-and-provisional-acceptance.md`](ADR-009-real-runtime-verification-and-provisional-acceptance.md)
* `src/taksitlio/runtime_verification/` — typed dependency probes + provisional gate
* `RemoteFastExtractor` / `StrictOpenAICompatibleEmbedder` — no silent lexical/stub fallback
* Redis + pgvector CI jobs must report `skipped = 0`
* `PROVISIONAL_ACCEPT` only when `real_redis_measured ∧ real_pgvector_measured ∧ real_fast_measured ∧ real_embedding_measured`
* Campaign Gate stays `CLOSED` until provisional clears → then `READY_TO_OPEN` (no campaign code here)

Reports: `evaluation/reports/adr008-p1-*.json` (honest `BLOCKED_DEPENDENCY` when live models absent).

## Reddedilen

* Threshold düşürmek · substring alias · kategori hardcode · kampanya açmak ·
  LexicalFallback’i production latency saymak · stub FAST’i gerçek runtime saymak ·
  sessiz in-memory / lexical fallback · Final ACCEPT

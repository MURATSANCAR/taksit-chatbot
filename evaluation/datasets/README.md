# Kategori Eşleme Değerlendirme Datasetleri

Bu klasör, Türkçe kategori eşleme (category-match) değerlendirme setlerinin
JSONL sürümlerini barındırır. Content ve şema referansları:

* Case şeması: [`evaluation/schemas/category_match_case.schema.json`](../schemas/category_match_case.schema.json)
* Dataset şeması: [`evaluation/schemas/category_match_dataset.schema.json`](../schemas/category_match_dataset.schema.json)
* Fixture katalog referansı: [`evaluation/fixtures/catalogs/category-fixture.v1.json`](../fixtures/catalogs/category-fixture.v1.json)
* ADR: [ADR-005](../../docs/adr/ADR-005-turkish-golden-set-and-semantic-evaluation.md)

## Split'ler

| Split | Dosya | Kullanım | Minimum boyut |
|-------|-------|----------|---------------|
| `development` | [`development/tr-category-dev.v1.jsonl`](development/tr-category-dev.v1.jsonl) | Geliştirme, keşifsel analiz, error bucket incelemesi | ≥ 150 |
| `validation` | [`golden/tr-category-validation.v1.jsonl`](golden/tr-category-validation.v1.jsonl) | Policy tuning, karşılaştırmalı A/B | ≥ 50 |
| `holdout` | [`golden/tr-category-holdout.v1.jsonl`](golden/tr-category-holdout.v1.jsonl) | Salt-okunur nihai kalite kapısı; tuning yasak (ADR-005 §6) | ≥ 50 |

`semantic_group_id` değerleri split'ler arasında **kesişmez** — böylece
"yakın kopya" hiçbir zaman ayrı split'e sızmaz. Bu kural
`taksitlio.evaluation.dataset.assert_split_integrity` tarafından kontrol edilir.

## Bootstrap generator

Sentetik case'ler `_generate_bootstrap.py` ile üretildi; her case
`privacy.synthetic = true` ve `annotation.status = DRAFT` taşır.
HUMAN_REVIEWED golden etiketi (≥ 2 reviewer) bu bootstrap'ta verilmez;
gerçek HUMAN_REVIEWED golden set MVP+1'de büyütülür.

```bash
python evaluation/datasets/_generate_bootstrap.py
```

## Alan sözlüğü

* `case_id` — dataset içi benzersiz kimlik.
* `semantic_group_id` — benzer yönergeleri kümeler; split-integrity
  sözleşmesi bu id üzerinden çalışır.
* `expected.status` — `MATCHED` / `AMBIGUOUS` / `NO_MATCH`.
* `expected.required_fixture_keys` — MATCHED case'ler için matcher'ın
  top-k içinde göstermesi gereken fixture key(ler).
* `expected.acceptable_fixture_keys` — MATCHED case'ler için kabul
  edilebilir alternatif fixture key(ler).
* `expected.forbidden_fixture_keys` — matcher'ın top-k'sında **kesinlikle
  bulunmaması** gereken fixture key(ler). (negation / category_change
  case'leri için).
* `dimensions.tags` — `direct_match / indirect_match / typo /
  colloquial / ambiguous / no_match / negation / multi_need /
  category_change / out_of_scope`.
* `privacy.synthetic` — sentetik ise `true`; PII kesinlikle
  bulunmamalıdır.

## Örnek çalıştırma

```bash
python -m taksitlio.evaluation.cli validate-dataset \
  --dataset evaluation/datasets/development/tr-category-dev.v1.jsonl \
  --check-split-integrity

python -m taksitlio.evaluation.cli run-category-eval \
  --dataset evaluation/datasets/development/tr-category-dev.v1.jsonl \
  --mode FULL
```

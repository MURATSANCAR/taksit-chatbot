# Kategori Eşleme Değerlendirme Yönetim Ekranları (MVP)

Semantic matcher'ın Türkçe kullanım altında **ölçülebilir** bir kalite
kapısıyla korunmasını sağlar. Referanslar:

* [ADR-005: Türkçe Golden Set ve Semantic Evaluation](../../docs/adr/ADR-005-turkish-golden-set-and-semantic-evaluation.md)
* [ADR-004: Dynamic Category Catalog & Semantic Matching](../../docs/adr/ADR-004-dynamic-category-catalog-and-semantic-matching.md)
* [ADR-006: Semantic Matcher Quality Hardening](../../docs/adr/ADR-006-semantic-matcher-quality-hardening.md)
* [ADR-007: End-to-End Understanding & Provisional Acceptance](../../docs/adr/ADR-007-end-to-end-understanding-and-provisional-acceptance.md)

Evaluation yalnızca ölçer. **Model, policy veya katalog değişikliğini asla
otomatik ACTIVE yapmaz** — challenger olarak kaydeder, AuditService +
admin onayı gerekir.

---

## 1. Dataset sürümleri

Dataset'ler `evaluation/datasets/<split>/<name>.vN.jsonl` altında
saklanır. Split'ler: `development` / `validation` / `holdout`.

Liste ekranı:

| Alan | Kaynak |
|------|--------|
| Dataset id | dosya adı + versiyon |
| Split | development / validation / holdout |
| Case sayısı | JSONL satırları |
| Semantic group ID sayısı | benzer yönergelerin gruplandırılması |
| Annotation dağılımı | DRAFT / SINGLE_REVIEWED / HUMAN_REVIEWED |
| Fixture katalog referansı | `fixture_catalog_id` + version |
| Immutable hash | dataset içerik hash'i (upload sırasında kilitlenir) |
| Kullanım | bu sürümü çalıştıran evaluation run'ları |

Aksiyonlar:

* **Validate** — schema + fixture key + split invariants kontrolü.
* **Freeze** — hash mühürlenir; artık düzenlenemez, yalnızca yeni versiyon açılır.
* **New version** — mevcutu klonlayıp taslak açar.
* **Diff versions** — case_id bazında add/remove/modify farkı.

Kural (ADR-005 §6): **Holdout üzerinde tuning yasaktır.** UI, holdout
seçildiğinde policy düzenleme akışını devre dışı bırakır ve
`Reason: HOLDOUT_LOCKED` gösterir.

---

## 2. Case yönetimi

Bir dataset seçili iken case listesi:

| Alan | Kaynak |
|------|--------|
| `case_id` | UUID |
| `semantic_group_id` | benzer varyasyonları kümeleme |
| `expected_status` | MATCHED / AMBIGUOUS / NO_MATCH |
| `expected_category_fixture_keys` | (fixture keys, e.g. `fixture.mobile-device`) |
| `required_candidate_keys` | zorunlu top-N adaylar |
| `forbidden_candidate_keys` | yasak adaylar |
| `dimensions.tags` | typo, colloquial, negation, multi_need, category_change, out_of_scope |
| `annotation.status` | DRAFT / SINGLE_REVIEWED / HUMAN_REVIEWED (≥2 reviewer) |
| `privacy.synthetic` | true → PII yok |

Case düzenleme paneli:

* Utterance (Türkçe, tek satır),
* Preferences hint listesi,
* Beklenen durum + fixture kategori beklentileri,
* Case dimensions (checkbox setleri),
* Annotation metadata (reviewer id / status).

Kural: bir case **golden HUMAN_REVIEWED** olarak işaretlenmek için en
az **iki bağımsız reviewer** onayı gerekir. Bootstrap sırasında
oluşan sentetik case'ler `annotation.status = DRAFT` veya
`SINGLE_REVIEWED` ile kalır; golden set etiketi verilmez.

---

## 3. Evaluation koşusu

“Run evaluation” formu:

* Model / matcher stack seçimi (embedding profile, policy code),
* Dataset + split seçimi,
* Mode: `FULL`, `LEXICAL_ONLY`, `VECTOR_ONLY`, `ALIAS_ONLY`, `DEGRADED`,
* Concurrency (worker sayısı) ve deadline_ms,
* Ham utterance log seçeneği (**opt-in debug only**) — varsayılan kapalı.

Koşu paneli:

* İlerleme (case count, ETA), P50/P95 latency çubuğu,
* Live metrik özet (status_accuracy, unsafe_auto_select_rate),
* Cancel butonu,
* Sonuçta ACCEPT / REJECT rozeti + tam metrik seti + error bucket listesi.

Standart rapor çıktısı `evaluation/reports/<run_id>.json`. **Raw
utterance içermez.** Debug modu açıksa ek olarak
`evaluation/private/<run_id>-debug.jsonl` — bu dosya `.gitignore`
tarafından repo dışı tutulur.

---

## 4. Karşılaştırma ve baseline

`compare-runs` ekranı iki (veya challenger + baseline) run seçer:

* Metrik farkı tablosu (delta + tolerans),
* Objective fonksiyonu skoru (config'ten okunan ağırlıklar; formül **kod içinde sabit değil**),
* Error bucket geçişleri (case_id kimlikleriyle),
* “Promote to baseline” butonu → yalnızca ACCEPT ise açık;
  * Yeni baseline `evaluation/baselines/category-match-baseline.vN.json`,
  * `AuditService` üzerinden aktör + rasyonel + prev/next hash'leri loglanır,
  * Model / policy / dataset değişimi tetiklemez — sadece kalite baseline pointer'ı taşır.

---

## 5. Policy tuning workflow'u

“Tune policy” ekranı **yalnızca validation split** üzerinden çalışır.

* Aday policy JSON (canonical alanlar; `SemanticMatchPolicyMapper` V008
  storage'a köprüler),
* Grid / manual override,
* Run challenger evaluation → validation split raporu,
* ACCEPT ise “Save as challenger policy” butonu policy'yi
  `semantic_match_policies` tablosuna **CHALLENGER** durumunda kaydeder.
* ACTIVE'e almak yalnızca ayrı bir ekranda, AuditService onaylı ve
  holdout üzerinde tek koşu doğrulaması gerektiren ayrı bir aksiyondur.
* Evaluation asla policy'yi kendi başına ACTIVE yapmaz.

---

## 6. Quality gate ekranı

Belirli bir dataset + baseline + config için gate özeti:

| Metrik | Threshold | Son run | Durum |
|--------|-----------|---------|-------|
| status_accuracy | ≥ config.threshold | … | ✓ / ✗ |
| unsafe_auto_select_rate | ≤ config.threshold | … | ✓ / ✗ |
| required_candidate_recall | ≥ threshold | … | ✓ / ✗ |
| forbidden_candidate_violation_rate | ≤ threshold | … | ✓ / ✗ |
| P95 latency (ms) | ≤ budget | … | ✓ / ✗ |
| ECE / Brier | ≤ threshold | … | ✓ / ✗ |

Aggregate ACCEPT/REJECT rozeti üstte gösterilir. Kampanya katmanına
geçiş için son çalıştırma **ACCEPT** olmalıdır (ADR-005 §10).

---

## 7. Error bucket analiz ekranı

Kova listesi (küme büyüklüğü + örnek case_ids):

* `wrong_top_1_when_should_match`
* `matched_when_should_be_ambiguous`
* `matched_when_should_be_no_match`  ← unsafe auto-select alt kümesi
* `ambiguous_when_should_match`
* `ambiguous_when_should_be_no_match`
* `no_match_when_should_match`
* `expected_category_missing_from_top_k`
* `forbidden_candidate_in_top_k`
* `latency_p95_over_budget`
* `dependency_failure`

Her kova için: case sayısı, örnek case_id linkleri, çoğunluk
`dimensions` etiketleri. Ham utterance yalnızca debug modda görünür.

---

## 8. Fixture katalog

`evaluation/fixtures/catalogs/category-fixture.vN.json` görüntüleyici.
Publish akışı, iki aşamalı publish helper (`prepare_embed_and_publish`)
ile izole bir catalog + revision + embedding stack'i kurar. Test
sonunda tüm fixture kayıtları temizlenir (integration test ile
doğrulanır).

---

## 9. Hardening ekranları (ADR-006)

Aşağıdaki ADR-006 bölümleri MVP admin akışına eklenmek üzere
tasarlandı. Alanlar mevcut listelere ek kolon olarak eklenebilir;
metrics panel `ProportionMetric` nesnesinin (`value`, `numerator`,
`denominator`, `support`, `support_status`, `confidence_interval_95`)
tamamını göstermelidir.

### 9.1 Retrieval diagnostics paneli

Her koşu için:

* `candidate_recall_at_pool` / `_at_5` / `_at_3` / `_at_2`
* `ranking_error_rate`, `decision_policy_error_rate`
* Retriever payı — alias / lexical / vector / use_case (case başına
  `retrieved_by` alanı üzerinden).
* Yeni error bucket'lar: `RETRIEVAL_MISS`, `RANKING_MISS`,
  `DECISION_FALSE_AMBIGUITY`, `DECISION_UNSAFE_MATCH`,
  `NEGATIVE_CONSTRAINT_VIOLATION`, `HIERARCHY_DUPLICATE_AMBIGUITY`.

### 9.2 Metric support göstergesi

Her rate metric için: `support_status` (`OK` / `LOW_SUPPORT` /
`NOT_APPLICABLE`) ve `%95 Wilson CI`. `LOW_SUPPORT` badge'i turuncu,
`NOT_APPLICABLE` gri gösterilir. `forbidden_candidate_violation_count`
alanı ayrı bir tam sayı olarak gösterilir — 1 dahi olsa promosyon
engellenir.

### 9.3 Negation / correction review kuyruğu

`review-status` CLI'nın döndürdüğü kuyruk admin'de sırayla açılır.
Reviewer, blind second review + adjudication akışıyla case'i
`HUMAN_REVIEWED` seviyesine çıkarır. Toplam `HUMAN_REVIEWED` sayısı
100'e ulaşana kadar quality gate en fazla `PROVISIONAL_ACCEPT`
verebilir; buraya `INSUFFICIENT_REVIEWED_DATA` göstergesi eklenmelidir.

### 9.4 Embedding challenger

`compare-embeddings` çıktısı yeni bir sekmede tutulur. Statü
`OK` / `EMBEDDING_DEPLOYMENT_UNAVAILABLE` /
`REJECTED_LEXICAL_FALLBACK` / `INVALID_DIMENSION` olur; hiçbir yolda
lexical fallback görünmezden gelinmez.


# Kategori Kataloğu Yönetim Ekranları (MVP)

Kategori isim/kod/eşiği hiçbir uygulama kodunda sabitlenmez. Aşağıdaki ekranlar
`category_catalogs`, `catalog_categories`, `category_localizations`,
`category_aliases`, `category_use_cases`, `category_attribute_links`,
`catalog_revisions`, `semantic_match_policies`,
`catalog_category_embeddings` ve `category_embedding_jobs` tablolarını yönetir.

ADR referansı: [ADR-004](../../docs/adr/ADR-004-dynamic-category-catalog-and-semantic-matching.md).

---

## 1. Katalog listesi ve seçme

**Amaç:** Aktif katalogların, primary_locale ve `published_revision` bilgisiyle
listelenmesi.

| Alan | Kaynak |
|------|--------|
| Katalog kodu | `catalog_code` |
| Görünen ad | `display_name` |
| Primary locale | `primary_locale` |
| Yayın revizyonu | `published_revision` |
| Draft revizyonu | `draft_revision` |
| Match policy | `match_policy_code` |
| Durum | `status` |

Aksiyonlar: yeni katalog oluşturma, alternate_locale ekleme, match policy
seçme, katalog metadata düzenleme, ARCHIVED / INACTIVE yapma.

---

## 2. Kategori ağacı

Ağaç görünümü + arama. Hiyerarşi `parent_id` üzerinden çizilir.

Kategori düzenleme paneli:

* `slug` (immutable oluşturma sonrası)
* `external_code` (opsiyonel eşleştirme)
* `parent_id` seçici (döngü ve max_depth kuralları önceden gösterilir)
* `semantic_description` — embedding kaynağı (boş bırakılırsa publish reddedilir)
* `ordering`
* Durum (DRAFT / ACTIVE / INACTIVE / ARCHIVED)
* Metadata JSON

---

## 3. Lokalizasyon + alias + use-case

Kategori seçiliyken sekmeler:

* **Localizations**: locale, display_name, description, synonyms[] listesi.
  Primary locale eksikse publish reddedilir.
* **Aliases**: locale + alias_text + alias_type (EXACT / PREFIX / FUZZY /
  SEMANTIC_HINT) + weight + status (soft delete için `INACTIVE`).
  Aynı katalog + locale altında aktif duplicate alias uyarısı.
* **Use-cases**: kullanım senaryosu metinleri (semantic hint besleme).
* **Attribute links**: `attribute_definition_id` (UUID) + importance.

---

## 4. Revizyon ve yayınlama (iki aşamalı publish)

Kural: **embedding'ler READY olmadan revision PUBLISHED olamaz.** Eski
`published_revision` yeni revision atomik olarak devralana kadar
matcher tarafından okunur — yayında hiçbir zaman "embedding'siz" bir
katalog görünmez. Bu akış ADR-004 (dynamic catalog) ve ADR-005
(evaluation) ile hizalıdır.

Revision durum makinesi:

```
DRAFT → PREPARING → embeddings READY → READY_TO_PUBLISH → PUBLISHED
                                    ↓                       ↓
                                  FAILED                 SUPERSEDED
```

Ekran akışı:

* Aktif drafts listesi (`catalog_revisions` DRAFT).
* **“Validate for publish”** butonu — validation raporunu (issues / warnings)
  gösterir, `catalog_revisions.validation_report` alanına yazar.
* **“Prepare revision”** — validation `ok` iken açılır:
  * DRAFT içeriği revision `pending` numarasıyla dondurulur,
  * Her locale için `catalog_snapshots` yazılır,
  * `catalog_revisions.status = PREPARING`,
  * Etkilenen kategoriler için `category_embedding_jobs` PENDING üretilir.
  * `published_revision` **değişmez** — mevcut yayın canlı kalır.
* Job kuyruğu READY olana kadar publish butonu **kilitli** kalır. Missing
  embedding sayacı UI'da gösterilir (`validation_report.missing_embeddings`).
* Embeddings READY olduğunda backend `mark_ready_to_publish` çağırır ve
  `status = READY_TO_PUBLISH` olur. Yalnızca bu durumda **“Publish now”**
  butonu açılır.
* **“Publish now”** atomik pointer switch yapar:
  * `published_revision` yeni revizyonu gösterir,
  * `catalog_revisions.status = PUBLISHED`, `published_at` yazılır,
  * Önceki PUBLISHED revizyonu `SUPERSEDED`'a taşınır.
* Herhangi bir adımda validation veya embedding job başarısız olursa
  revision `FAILED` olur; eski publish etkilenmez.

Backend sözleşmesi: yayıncı servis `prepare_embed_and_publish`
(`taksitlio.category_catalog.publish_pipeline`) yardımcısını kullanır —
`prepare_revision → embed workers → mark_ready_to_publish → publish_revision`.
Publish-then-embed akışı yasaktır.

---

## 5. Semantic match politikası

`semantic_match_policies` yönetim ekranı canonical alan isimleriyle
çalışır (ADR-005 §11). V008 tablosu tarihsel olarak `minimum_score` /
`clarify_score_gap` sütunlarını taşıyabilir; okuma/yazma
`SemanticMatchPolicyMapper`
(`src/taksitlio/semantic_matching/policy.py`) üzerinden köprülenir,
destructive rename yoktur.

| Alan (canonical) | Anlamı | Storage notu |
|------------------|--------|--------------|
| `minimum_candidate_score` | Aday listesine girme eşiği | V008 sütunu `minimum_score` — mapper okur |
| `minimum_auto_select_score` | Otomatik seçim skor tabanı | (aynı sütun; mapper) |
| `minimum_auto_select_gap` | AMBIGUOUS'a düşmek için en az fark | V008 sütunu `clarify_score_gap` — mapper okur |
| `maximum_candidates` | Session'a yazılacak maksimum aday | — |
| `alias_weight` / `lexical_weight` / `vector_weight` / `use_case_weight` / `hierarchy_weight` | Hybrid skor ağırlıkları (matcher normalize eder) | — |
| `exact_alias_can_auto_select` | Degraded modda EXACT alias auto-select izni | — |
| `allow_lexical_degraded_mode` | Embedding gateway erişilemezse degraded mode | — |
| `maximum_embedding_timeout_ms` | Query embedding timeout | — |
| `cache_ttl_seconds` | Match cache TTL | — |
| `require_semantic_description` | Publish validasyonu | — |
| `max_depth` | Publish validasyonu | — |
| `fuzzy_min_similarity` | FUZZY alias eşiği | — |
| `policy_version` | Cache key parçası; her ağırlık değişikliği +1 | — |

Policy düzenleme, evaluation koşusu tarafından ACCEPT vermeden **ACTIVE**'e
alınmaz — challenger olarak kaydedilir, admin + AuditService onayı
gerekir (bkz. `admin/specs/category-evaluation-admin-screens.md`).

---

## 6. Embedding jobs

Job kuyruğu paneli:

* Filtre: `status` (PENDING / READY / FAILED / STALE), `catalog_revision`,
  `embedding_profile_id`, `locale`.
* “Retry FAILED” aksiyonu — job attempts'i sıfırlar, PENDING'e alır.
* “Regenerate for catalog X revision Y” aksiyonu — mevcut READY kayıtları
  STALE yapıp yeni PENDING üretir.
* Embedding profili değiştirmek: yeni PROFILE ID'ler için ayrı job seti
  otomatik oluşur; eski kayıtlar `STALE` işaretlenir.

---

## 7. Matcher deneme paneli

Türkçe / lokalize test cümlelerini seçili katalog + locale + policy ile
matcher'a gönderip:

* `MatchQuery` çıktısını,
* Adayları, skoru ve `SignalBreakdown` (alias/lexical/vector/use-case/hierarchy)
  değerlerini,
* Decision status + score_gap değerini,
* `degraded` bayrağını,
* `duration_ms` süresini gösterir.

Bu ekran yalnızca simülasyondur; conversation state'e yazmaz.

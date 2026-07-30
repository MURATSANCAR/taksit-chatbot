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

## 4. Revizyon ve yayınlama

* Aktif drafts listesi (`catalog_revisions` DRAFT).
* “Validate for publish” butonu — validation rapor (issues / warnings)
  gösterilir, kaydedilir.
* Publish onayı yalnızca validation `ok` iken açılır. Publish sonrası:
  * `published_revision` güncellenir,
  * `catalog_revisions` PUBLISHED satırı `published_at` ile kaydedilir,
  * Etkilenen tüm kategoriler için `category_embedding_jobs` PENDING oluşturulur.

---

## 5. Semantic match politikası

`semantic_match_policies` yönetim ekranı:

| Alan | Anlamı |
|------|--------|
| `minimum_score` | Aday olabilme eşiği |
| `clarify_score_gap` | AMBIGUOUS eşiği |
| `maximum_candidates` | Session'a yazılacak maksimum aday |
| `alias_weight` / `lexical_weight` / `vector_weight` / `use_case_weight` / `hierarchy_weight` | Hybrid skor ağırlıkları (matcher normalize eder) |
| `allow_lexical_degraded_mode` | Embedding gateway erişilemezse degraded mode |
| `cache_ttl_seconds` | Match cache TTL |
| `require_semantic_description` | Publish validasyonu |
| `max_depth` | Publish validasyonu |
| `fuzzy_min_similarity` | FUZZY alias eşiği |
| `policy_version` | Cache key parçası; her ağırlık değişikliği +1 |

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

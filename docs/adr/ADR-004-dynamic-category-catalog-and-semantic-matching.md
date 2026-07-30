# ADR-004: Dinamik Kategori Kataloğu ve Semantic Matcher

## Durum

Kabul edildi — MVP genişletmesi.

## Bağlam

Taksitlio chatbotunda kampanya öneri katmanı, kullanıcının serbest Türkçe metnini
bir kategori kataloğundaki mevcut kategoriyle eşleştirmeye ihtiyaç duyar.

Mevcut V003 şeması (`categories`, `category_embeddings`, `category_match_policies`)
tek revizyonlu, tek dilli, tek eşik politikalı, tek embedding profilli bir yapıyı
varsayar. FAST model kategori kodu üretmez (ADR-001 §5). MVP büyüdükçe:

* Yeni kategori/alt-kategori (Seyahat, Sağlık, Otomotiv, Eğitim) ekleme,
* Türkçe dışı (or dialect-specific) lokalizasyon,
* Eşanlamlı ve marka bazlı **alias**'ların ürün taksonomisine sızmadan yönetilmesi,
* Embedding profili değiştiğinde eski vektörlerin **STALE** olarak kalması,
* Kategori kodu enum'larını uygulama kodundan tamamen çıkarmak,
* Match sonuçlarının conversation state'e **manager tarafından** yazılması

gereksinimleri ortaya çıkıyor. Ayrıca:

* Match sonucu **AMBIGUOUS** çıktığında ModelRouter fallback değil clarification
  tarafında karar vermelidir (ADR-001 §5, ADR-003 §12).
* Embedding sunucusu erişilemez olduğunda sistem hiç match yapmamak yerine
  alias + lexical **degraded mode**'a düşmelidir (policy izin veriyorsa).
* Match uygulaması conversation state'i doğrudan mutasyona uğratmaz — bunu yalnızca
  `ConversationStateManager` yapar (ADR-003 §8).
* Kategori isim/kod/eşiği **hiçbir yerde kodda sabitlenmez**; DB-driven kalır.

## Karar

### 1) Katalog + revizyonlu publish

* `category_catalogs` — mantıksal katalog (locale ana, opsiyonel alternate locales,
  match_policy_code, published_revision).
* `catalog_categories` — kategori düğümleri, hiyerarşik (parent_id), status
  DRAFT/ACTIVE/INACTIVE/ARCHIVED, `semantic_description` (embedding kaynağı),
  bakış açısı label'i **kod değil UUID**.
* `category_localizations` — locale başına display_name, description, synonyms.
* `category_aliases` — alias metni + tipi (EXACT, PREFIX, FUZZY, SEMANTIC_HINT),
  kategori bazında.
* `category_use_cases` — kullanım senaryoları (semantic hint için).
* `category_attribute_links` — attribute_definition_id (UUID) referansları;
  attribute catalog tablosunu zorunlu kılmadan; müşteri tarafı MVP sonrası ekler.
* Katalog **atomically publish** edilir: `catalog_revisions(revision, status)`
  publish edildiğinde `published_revision` işaret eder.
* Matcher yalnızca `PUBLISHED` snapshot'ı okur.

### 2) Semantic match policy

* Yeni tablo `semantic_match_policies` — V003’ün `category_match_policies` ile
  **isim çakışmasını önlemek için ayrı**.
* Canonical domain alanları (ADR-005): `minimum_candidate_score`,
  `minimum_auto_select_score`, `minimum_auto_select_gap`, `maximum_candidates`,
  ağırlıklar, `allow_lexical_degraded_mode`, `exact_alias_can_auto_select`,
  `maximum_embedding_timeout_ms`, `policy_version`.
* V008 storage sütunları `minimum_score` / `clarify_score_gap` mapper ile okunur
  (`SemanticMatchPolicyMapper`); destructive rename yok.
* Tek seed: `CATEGORY_MATCH_DEFAULT` (kategori seed yok).

### 3) Embedding jobs + iki aşamalı publish

Publish sırası:

`DRAFT → PREPARING → embedding READY → READY_TO_PUBLISH → PUBLISHED`

Published revision oluşturulduktan sonra embedding üretilmez. Hazır olmayan
revision canlı matcher’a açılmaz. Eski published, yeni draft PREPARING iken
çalışmaya devam eder.

* `catalog_category_embeddings` — kategori × revision × locale × profile başına
  vektör; boyut `embedding_dimension`; taşınabilirlik için `DOUBLE PRECISION[]`
  (pgvector `VECTOR` extension aynı ortamda kurulabilirse opsiyonel kolon olarak
  eklenebilir; migration `CREATE EXTENSION IF NOT EXISTS vector` denemesi yapar
  ama hata durumunda plain array yolu geçerlidir).
* `category_embedding_jobs` — dedupe anahtarı:
  `(category_id, catalog_revision, locale, embedding_profile_id, content_hash)`.
* `content_hash = SHA-256(projection_text)`.
* Job statüsleri: PENDING, READY, FAILED, STALE (embedding profili değişince
  önceki vektör STALE'e düşer).
* Bounded retry (`max_attempts`).

### 4) Matcher

* Deterministic **hybrid scoring**:

  ```
  hybrid = w_alias * alias_score
         + w_lex   * lexical_score
         + w_vec   * vector_score
         + w_use   * use_case_score
         + w_hier  * hierarchy_score
  ```

  Weights matcher'da hardcode edilmez; `SemanticMatchPolicy`'den okunur ve
  1.0'a normalize edilir. Ağırlıklardan biri 0 ise sinyali by-pass eder.

* **Alias retriever** modları: EXACT, PREFIX, FUZZY (basit char n-gram similarity),
  SEMANTIC_HINT (semantik hint olarak kullanılır, doğrudan match değil).

* **Vector retriever** in-memory testte
  `taksitlio.embeddings.vectors.bag_of_chars_embedding` + cosine kullanır.
  Production'da `EmbeddingGateway` ProfileEmbedder üzerinden llama.cpp çağırır.

* **DecisionPolicy** SemanticMatchPolicy'den kaynak alır ve şu statüleri döner:
  `MATCHED` | `AMBIGUOUS` | `NO_MATCH` | `CATALOG_EMPTY` | `CATALOG_UNAVAILABLE`.

* **Cache** anahtarı:
  `SHA-256(normalize(input) + catalog_revision + embedding_profile_id +
   policy_version + locale)`.
  **Ham kullanıcı metni asla anahtar olarak kullanılmaz.** NoOp ve in-memory
  implementasyon; production Redis-based ayrı katman.

* **Degraded mode**: EmbeddingGateway failure durumunda `policy.allow_lexical_degraded_mode`
  true ise vektör sinyali by-pass edilir, sonuçta `degraded=True` işareti taşır.

### 5) Conversation state entegrasyonu

* Matcher **hiçbir zaman** ConversationStateManager veya repository'yi doğrudan
  yazmaz.
* `CategoryResolutionApplier` — `CategoryMatchResult` + `ConversationStateManager`
  alır; **yalnızca** `category_resolution.selected_category_id`,
  `candidates`, `catalog_id`, `catalog_revision`, `match_status` alanlarını
  `apply_model_update` ile yazar. Embedding vektörleri, projection text ve alias
  listesi conversation state'e sızmaz.
* `CategoryResolution.selected_category_id` MVP için `str | int | None` union'a
  genişletildi (UUID desteği; mevcut testler kırılmaz).

### 6) System confidence sinyalleri

* `SystemConfidenceEvaluator.SemanticSignals` opsiyonel yeni alanlar:
  `semantic_match_status`, `semantic_degraded`, `catalog_consistent`.
* `AMBIGUOUS` veya `NO_MATCH` veya `semantic_degraded` durumunda structural
  skora çarpansal (yumuşak) ceza uygulanır. NeutralSemanticSignalProvider davranışı
  değişmez.

### 7) Dinamik runtime kabul kriteri

Boş bir katalog runtime içinde bulunduğunda:

1. Yönetim akışıyla yeni kategori, lokalizasyon, alias, use-case eklenmesi,
2. Embedding job'unun tamamlanması,
3. Katalog revizyonunun publish edilmesi,
4. Matcher'ın **restart olmadan** yeni kategoriyi match etmesi,
5. Kategorinin INACTIVE veya ARCHIVED yapılmasıyla artık match etmemesi

sağlanmalıdır. Bu kabul kriteri
`tests/integration/semantic_matching/test_dynamic_runtime_category.py` ile
otomatik doğrulanır (in-memory stack, Postgres gerektirmez).

## Reddedilen alternatifler

* **Kategori enum'u / sabit liste** — Yeni kategori eklemek deploy gerektirir.
* **Prompt içine tüm kategori listesini gömmek** — Prompt şişer, privacy ve drift.
* **Sadece keyword eşleştirme** — Dolaylı ihtiyaçları kaçırır.
* **Sadece vector similarity** — Alias/exact sinyallerini kaybeder.
* **LLM'den doğrudan kategori ID istemek** — Katalog değişince prompt değişir (ADR-001).
* **Her request'te tüm kategorileri LLM'e göndermek** — Latency ve maliyet.
* **Kategori değişikliğinde uygulama restart** — Dinamik katalog kabul kriterini bozar.
* **Embedding başarısızken eski/yeni katalog verilerini karıştırmak** — Tutarsız revision.
* **Matcher session state'e yazsın** — ADR-003 §8 ihlali.
* **In-memory global cache raw input üzerinden** — Privacy + collision.
* **V003 `categories` tablosunu genişletme** — Legacy pipeline sözleşmesiyle çakışır.

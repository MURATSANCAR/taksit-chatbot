# AI Yönetim Paneli — Ekran Spesifikasyonları (MVP)

Model isimleri, quantization, timeout, confidence ve promptlar kodda sabitlenmez.
Aşağıdaki ekranlar `ai_model_profiles`, `ai_task_routes`, `ai_confidence_policies`,
`ai_timeout_policies`, `ai_prompt_versions` ve `ai_schema_versions` tablolarını yönetir.

---

## 1. Model profilleri

**Amaç:** FAST / FALLBACK / challenger profillerini görüntülemek ve güncellemek.

Alanlar:

| Alan | Kaynak |
|------|--------|
| Profil kodu | `profile_code` (salt okunur oluşturma sonrası) |
| Görünen ad | `display_name` |
| Provider | `provider_type` |
| Endpoint | `endpoint_url` |
| Model referansı | `model_reference` |
| Quantization | `configuration.quantization` |
| Timeout (ms) | `timeout_ms` |
| Parallel slots | `parallel_slots` |
| Temperature | `temperature` |
| Context / max output | `context_limit`, `max_output_tokens` |
| Durum | `ACTIVE` / `INACTIVE` / `CHALLENGER` / `DEPRECATED` |

Aksiyonlar:

* FAST modeli görüntüleme
* FALLBACK modeli görüntüleme
* Quantization seçme
* Timeout değiştirme
* Parallel slot değiştirme
* Modeli aktif/pasif yapma
* Challenger’ı `ACTIVE` yapıp mevcut FAST’i `CHALLENGER` veya `DEPRECATED` yapma (tek tık swap + `ai_task_routes` güncelleme)

---

## 2. Model karşılaştırma

**Amaç:** Aynı Türkçe mesajı iki profile göndermek; çıktı, doğruluk ve süreyi yan yana göstermek.

Akış:

1. Mesaj + opsiyonel session özeti gir
2. Profil A + Profil B seç
3. `ModelGateway` her iki profile çağrı yapsın
4. JSON çıktıları diff ile göster
5. Latency (ms), schema geçerliliği, confidence skorları
6. “Challenger’ı aktif modele dönüştür” aksiyonu

---

## 3. Prompt ve schema yönetimi

* Prompt versiyonlarını listele (`ai_prompt_versions`)
* Aktif promptu değiştir (önceki `is_active=false`)
* JSON Schema versiyonlarını yönet (`ai_schema_versions`)
* Eski versiyona geri dön
* `NEED_UNDERSTANDING` için aktif prompt + schema eşlemesini göster

---

## 4. Güven politikaları

Kaynak: `ai_confidence_policies` + `ai_timeout_policies`

* Confidence eşiğini değiştir (`minimum_confidence`)
* Clarification politikasını değiştir (`prefer_clarification_when_ambiguous`, score gap)
* FALLBACK kurallarını değiştir (invalid schema, conflict, multiple needs, budget confusion)
* Model timeout politikasını değiştir (`primary_timeout_ms`, `fallback_timeout_ms`, `total_budget_ms`)

Değişiklikler A/B sonuçlarına göre kaydedilir; deploy gerekmez.

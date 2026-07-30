# Taksitlio Chatbot — MVP Mimarisi

Bu belge Taksitlio mobil chatbotunun MVP mimarisini tanımlar. Kampanya verileri, kategori listeleri, model isimleri, promptlar, confidence eşikleri ve routing kararları kod içerisine sabitlenmez; yönetim panelinden ve veritabanından yönetilir.

---

## Üst düzey akış

```text
Taksitlio Mobil Uygulama
          ↓
Chat API
          ↓
Conversation State Manager
          ↓
Dynamic Model Router
          ↓
FAST Türkçe Anlama Modeli
          │
          ├── Güvenli sonuç
          │
          ├── Clarification
          │
          └── Büyük model fallback
          ↓
Dinamik kategori kataloğu
          ↓
Semantic category matcher
          ↓
Kampanya retrieval
          ↓
Deterministik uygunluk motoru
          ↓
Dinamik ranking motoru
          ↓
Grounded cevap oluşturma
          ↓
Üyelik CTA
```

---

# Gerçek Zamanlı Türkçe Anlama Katmanı

## 1. Amaç

Taksitlio chatbotunda kullanıcının günlük Türkçeyle yazdığı mesajlar saniyeler içinde anlaşılmalıdır.

Sistem şu tür ifadeleri doğal biçimde çözebilmelidir:

> Telefon bakıyoruz, 40 civarına çıkabiliriz.

> Kızım üniversiteye başlayacak, hafif bir şey olsun, 35’i geçmesin.

> Çok pahalı olmayan, kamerası sağlam bir telefon arıyorum.

> Peşin zor olur, aylık ödemesi düşük olsun.

> Bilgisayar mı tablet mi karar veremedim; okulda kullanacağım.

Bu mesajlar kelime eşleştirme veya sabit kategori listesiyle çözülmeyecek. Küçük ve hızlı bir local LLM, kullanıcının gerçek ihtiyacını yapılandırılmış bir ihtiyaç profiline dönüştürecek.

---

## 2. İki katmanlı model mimarisi

```text
Kullanıcı mesajı
        ↓
FAST Türkçe Anlama Modeli
        ↓
Yapılandırılmış ihtiyaç profili
        ↓
Güven ve belirsizlik kontrolü
        │
        ├── Yeterli güven
        │        ↓
        │   Kategori ve kampanya motoru
        │
        └── Düşük güven
                 ↓
        Büyük FALLBACK Model
                 ↓
        Kesinleştirilmiş ihtiyaç profili
```

### FAST model

Her kullanıcı mesajında çalışır.

Görevleri:

* Türkçe serbest metni anlamak
* Kullanıcı niyetini çıkarmak
* Bütçeyi ve bütçe biçimini anlamak
* Kullanım amacını belirlemek
* Ürün özelliklerini çıkarmak
* Taksit ve ödeme tercihlerini anlamak
* Önceki mesajdaki bilgilerin değişip değişmediğini belirlemek
* Belirsizlik ve çelişkileri işaretlemek
* Gerekli olduğunda açıklayıcı soru önermek

FAST model kampanya seçmez, kampanya koşulu yorumlamaz ve kullanıcıya finansal sonuç üretmez.

### FALLBACK model

Yalnızca şu durumlarda çağrılır:

* FAST modelin güveni düşükse
* Mesaj birden fazla farklı ihtiyacı içeriyorsa
* Kategori adayları birbirine çok yakınsa
* Kullanıcı bütçe ile aylık ödeme tutarını karıştırdıysa
* Yeni mesaj önceki konuşmayla çelişiyorsa
* FAST model geçerli yapılandırılmış çıktı veremediyse
* Kullanıcının ifadesi çok dolaylı veya karmaşıksa

Mevcut büyük local model bu görev için kullanılır. Böylece büyük model her mesajda çalışmaz ve sistemin genel hızını düşürmez.

---

## 3. POC için FAST model adayı

İlk aday:

```text
Qwen3.5-4B
Quantization: Q4_K_M veya Q5_K_M
Görev: Türkçe ihtiyaç ve niyet çıkarımı
Çıktı: Kısa JSON
Thinking: Kapalı
```

Qwen3.5-4B, 4 milyar parametreli bir modeldir. Resmî model kartında 201 dil ve lehçe desteği ile düşük gecikmeli, yüksek throughput odaklı hibrit mimari belirtilmektedir. Modelin çeşitli local inference araçları için quantization seçenekleri de bulunmaktadır. Bununla birlikte resmî kaynaklar yalnızca Türkçeye özel bir doğruluk garantisi vermediği için seçim, hazırlanacak gerçek Türkçe test setiyle doğrulanacaktır.

İkinci aday:

```text
Qwen3-4B-Instruct-2507
```

Bu model yalnızca non-thinking modunda çalışır, 4 milyar parametreye sahiptir, çok dilli anlama alanında geliştirmeler içerir ve `llama.cpp` dahil local inference araçları tarafından desteklenir.

Model seçimi kod içinde sabitlenmeyecektir. İki model aynı Türkçe test setinde karşılaştırılacaktır.

---

## 4. Dinamik model yönetimi

Model isimleri ve ayarları environment dosyasına veya uygulama koduna gömülmeyecek.

### `ai_model_profiles`

```text
id
profile_code
display_name
provider_type
endpoint_url
model_reference
task_type
context_limit
max_output_tokens
temperature
timeout_ms
parallel_slots
status
configuration
created_at
updated_at
```

Örnek profiller:

```text
FAST_UNDERSTANDING
DEEP_UNDERSTANDING
RESPONSE_GENERATION
EMBEDDING
RERANKING
```

### `ai_task_routes`

```text
id
task_code
primary_model_profile_id
fallback_model_profile_id
confidence_policy_id
timeout_policy_id
status
```

Örnek:

```json
{
  "task_code": "NEED_UNDERSTANDING",
  "primary_model_profile": "FAST_UNDERSTANDING",
  "fallback_model_profile": "DEEP_UNDERSTANDING"
}
```

Bu yapı sayesinde ileride başka bir model daha başarılı çıkarsa:

* Kod değişmez.
* Mobil API değişmez.
* Chat akışı değişmez.
* Uygulama yeniden geliştirilmez.
* Yönetim panelinden aktif model değiştirilir.

İlgili şema: [`db/migrations/V001__ai_model_management.sql`](../../db/migrations/V001__ai_model_management.sql)

---

## 5. FAST modelin çıktı sözleşmesi

Modelin kategori kodu üretmesi istenmeyecektir. Çünkü kategori listesi zamanla değişebilir.

Yanlış çıktı:

```json
{
  "category": "MOBILE_PHONE"
}
```

Doğru çıktı:

```json
{
  "intent": {
    "type": "PRODUCT_PURCHASE",
    "confidence": 0.97
  },
  "need_description": "kamera kalitesi iyi ve taksitle alınabilecek mobil cihaz",
  "budget": {
    "type": "APPROXIMATE",
    "value": 40000,
    "minimum": null,
    "maximum": null,
    "monthly_payment": null,
    "currency": "TRY"
  },
  "preferences": [
    {
      "concept": "camera_quality",
      "importance": 0.88
    },
    {
      "concept": "installment",
      "importance": 0.94
    }
  ],
  "usage_context": [],
  "entities": [],
  "ambiguities": [],
  "clarification": {
    "required": false,
    "question_intent": null
  },
  "confidence": 0.95
}
```

Sonrasında `need_description`, dinamik kategori kataloğuyla semantic olarak eşleştirilir.

Bu nedenle sisteme ileride:

* Seyahat
* Mobilya
* Eğitim
* Sağlık
* Sigorta
* Otomotiv

kategorileri eklendiğinde FAST model promptunun değiştirilmesi gerekmez.

JSON Schema: [`src/taksitlio/schemas/need_profile.schema.json`](../../src/taksitlio/schemas/need_profile.schema.json)

---

## 6. Konuşma güncelleme sistemi

Kullanıcı her mesajda bütün ihtiyacını baştan anlatmak zorunda değildir.

İlk mesaj:

> Telefon bakıyoruz, 40 bin civarı.

İkinci mesaj:

> Kamerası iyi olsun.

Üçüncü mesaj:

> Bütçeyi 50’ye çıkarabiliriz.

FAST model üçüncü mesajda yeni bir bağımsız ihtiyaç oluşturmayacak. Mevcut konuşma durumuna uygulanacak değişikliği çıkaracaktır:

```json
{
  "operation": "UPDATE",
  "updates": [
    {
      "field": "budget.value",
      "old_value": 40000,
      "new_value": 50000
    }
  ],
  "preserve": [
    "need_description",
    "preferences.camera_quality"
  ],
  "confidence": 0.98
}
```

Redis’te tutulan yapılandırılmış session state güncellenir. Tüm konuşma geçmişi her seferinde modele gönderilmez.

JSON Schema: [`src/taksitlio/schemas/conversation_update.schema.json`](../../src/taksitlio/schemas/conversation_update.schema.json)

---

## 7. Hızlı çıkarım ayarları

FAST model yalnızca küçük bir görev yapacaktır.

```text
Context hedefi: 2.048–4.096 token
Çıktı hedefi: 60–120 token
Temperature: 0–0.1
Thinking: Kapalı
Streaming: Kapalı
JSON Schema: Zorunlu
Model timeout: 3 saniye
Retry: Aynı modelde yok
Prompt geçmişi: Gönderilmez
Session özeti: Gönderilir
Kampanya kayıtları: Gönderilmez
```

Modelin görevi uzun cevap yazmak değil, kısa ve güvenilir ihtiyaç profili oluşturmaktır.

---

## 8. 48 core / 256 GB RAM performans hedefi

Aşağıdaki değerler garanti edilmiş benchmark sonucu değil, POC sırasında doğrulanacak mühendislik hedefleridir.

```text
FAST model anlama P50:       0,8–2,0 saniye
FAST model anlama P95:       3 saniye altı
Kampanya retrieval:          50–200 ms
Uygunluk ve ranking:         20–100 ms
Toplam öneri P50:            1,5–3 saniye
Toplam öneri P95:            5 saniye altı
```

Model 4B parametreli olduğu için mevcut 35B/3B-active büyük modele göre kısa ihtiyaç çıkarımında daha düşük gecikme hedeflenmektedir; kesin süre CPU modeli, RAM bant genişliği, quantization ve paralel istek sayısıyla yapılacak benchmark sonucunda belirlenecektir.

---

## 9. Model router

Model seçimini `ModelRouter` yapacaktır.

```text
Mesaj
  ↓
FAST model
  ↓
Çıktı doğrulama
  ↓
Confidence Policy
  │
  ├── confidence ≥ eşik
  │       ↓
  │   Devam et
  │
  ├── açıklayıcı soru daha doğru
  │       ↓
  │   Kullanıcıya tek soru sor
  │
  └── büyük model gerekli
          ↓
      FALLBACK model
```

Yönlendirme politikası veritabanından yönetilecektir.

Örnek:

```json
{
  "minimum_confidence": 0.78,
  "maximum_category_score_gap_for_clarification": 0.08,
  "fallback_on_invalid_schema": true,
  "fallback_on_conflict": true,
  "fallback_on_multiple_needs": true
}
```

Bu değerler yönetim panelinden ve A/B test sonuçlarından güncellenebilecektir.

Uygulama iskeleti:

* [`src/taksitlio/model_gateway/`](../../src/taksitlio/model_gateway/)
* [`src/taksitlio/model_router/`](../../src/taksitlio/model_router/)

---

## 10. Türkçe test ve kabul seti

Model yalnızca “Türkçe destekliyor” açıklamasına dayanarak seçilmeyecektir.

En az 1.000 gerçekçi Türkçe cümleden oluşan bir değerlendirme seti hazırlanacaktır:

```text
200 açık ürün talebi
150 dolaylı ihtiyaç ifadesi
150 yazım hatalı günlük konuşma
100 bütçe ve fiyat aralığı
100 taksit ve aylık ödeme ifadesi
100 kategori belirtilmeyen ihtiyaç
75 birden fazla ürün talebi
50 çelişkili mesaj
50 konu dışı mesaj
25 konuşma içi fikir değişikliği
```

Örnekler:

> tlfn alıcaz 40 bin fln bütçe var

> okul için hafif bişey lazım bilgisayar gibi ama tablet de olabilir

> peşin 30 var kalanını aylık ödesek

> samsung düşünüyordum ama iphone kampanyası iyiyse o da olur

> yok telefonu boşver bilgisayar bakalım

Ölçülecek metrikler:

```text
Niyet doğruluğu
Bütçe değeri doğruluğu
Bütçe türü doğruluğu
Tercih çıkarımı
Konuşma güncelleme doğruluğu
Clarification doğruluğu
Geçerli JSON oranı
P50 ve P95 gecikme
Eşzamanlı istek kapasitesi
Fallback oranı
```

Kabul hedefleri:

```text
Bütçe doğruluğu:             ≥ %98
Niyet doğruluğu:             ≥ %96
Konuşma güncelleme doğruluğu:≥ %95
Geçerli JSON:                %100
FAST model kullanım oranı:   ≥ %85
Büyük modele fallback:       ≤ %15
FAST P95:                    < 3 saniye
```

---

## 11. Yönetim paneline eklenecek AI ekranları

### Model profilleri

* FAST modeli görüntüleme
* FALLBACK modeli görüntüleme
* Quantization seçme
* Timeout değiştirme
* Parallel slot değiştirme
* Modeli aktif/pasif yapma

### Model karşılaştırma

* Aynı mesajı iki modele gönderme
* Çıktıları yan yana görme
* Doğruluk ve süreyi karşılaştırma
* Challenger modeli aktif modele dönüştürme

### Prompt ve schema yönetimi

* Prompt versiyonlarını görüntüleme
* Aktif promptu değiştirme
* JSON Schema versiyonlarını yönetme
* Eski versiyona geri dönme

### Güven politikaları

* Confidence eşiğini değiştirme
* Clarification politikasını değiştirme
* FALLBACK kurallarını değiştirme
* Model timeout politikasını değiştirme

Ekran spesifikasyonları: [`admin/specs/ai-admin-screens.md`](../../admin/specs/ai-admin-screens.md)

---

## 12. Güncellenmiş MVP mimarisi

Kampanya verileri, kategori listeleri, model isimleri, promptlar, confidence eşikleri ve routing kararları kod içerisine sabitlenmeyecektir.

Bu katman sayesinde sistem static olmaktan çıkar; her mesajda ağır modeli çalıştırmadan Türkçe konuşmalar saniyeler içinde anlaşılır.

---

## 13. MVP model kararı

Başlangıçta iki FAST aday kurulacaktır:

```text
Aday A: Qwen3.5-4B Q4_K_M
Aday B: Qwen3-4B-Instruct-2507 Q4_K_M
```

Mevcut büyük model:

```text
DEEP_UNDERSTANDING fallback
```

olarak kullanılacaktır.

Kesin FAST model kararı, Türkçe golden dataset ve 1, 4, 8 eşzamanlı istek benchmarkından sonra verilecektir.

Böylece model tercihi varsayıma değil, Taksitlio’nun gerçek Türkçe kullanıcı konuşmalarındaki doğruluk ve hız sonuçlarına dayanacaktır.

---

## Sonraki teknik adımlar

1. Dinamik `ModelGateway`, `ModelRouter` ve model profil tabloları (bu sürümde iskelet oluşturuldu)
2. Conversation State Manager (Redis session state)
3. Semantic category matcher
4. Kampanya retrieval + uygunluk + ranking
5. Grounded cevap oluşturma
6. Türkçe golden dataset (1.000+ cümle) ve benchmark

# ADR-012: Answer Integrity, Claim Grounding, and Recommendation Safety

## Durum

**Accepted — P0 skeleton + zero-tolerance gates (2026-07-31).**

Bu ADR, ADR-010 (gerçek katalog / kampanya / ödeme) ve ADR-011
(clarification-first / LLM routing) üzerine **cevap bütünlüğü** katmanını
tanımlar. Ürün araması çalışır; finansal ürün sunan kurumsal güven seviyesi
için claim grounding, alan bazlı güven, deterministik response composer ve
recommendation safety zorunludur.

P0 kod iskeleti land edildi:

- `src/taksitlio/answer_integrity/`
- `src/taksitlio/claim_validation/`
- `src/taksitlio/recommendation_safety/`
- `db/migrations/V023__answer_integrity_claim_grounding.sql`

`GroundedResponseGenerator` Final Claim Validator + template fallback ile
bağlandı. Sıfır-tolerans unit/acceptance testleri mevcut.

## Bağlam

ADR-010 ve ADR-011 sonrası temel güçlüdür:

- Production hardcode / sahte veri yasağı
- Merchant–banka anlaşmasının açık ilişkiyle tutulması
- Versioned oran kayıtları
- Provenance / freshness etiketleri
- Clarification-first; emin olunmayan sorguda doğrudan sonuç yok
- LLM yalnız structured semantic patch üretir; fiyat/oran uyduramaz

Eksik katman: gösterilen her kritik bilginin **kanıtlı**, **alan bazlı
güvenilir**, **çelişkisiz** ve **LLM’den bağımsız doğrulanmış** olması.
Tek genel `overall_confidence` veya LLM’in serbest metin cevabı bu barı
geçmez.

İlgili ADR’ler:

| ADR | Rol |
|---|---|
| [ADR-005](ADR-005-turkish-golden-set-and-semantic-evaluation.md) | Golden / semantic eval temeli |
| [ADR-007](ADR-007-end-to-end-understanding-and-provisional-acceptance.md) | Constraint validator, negation |
| [ADR-009](ADR-009-real-runtime-verification-and-provisional-acceptance.md) | Campaign Gate (kişisel onay) |
| [ADR-010](ADR-010-real-product-catalog-campaigns-and-fast-offers.md) | Katalog, finans, ranking |
| [ADR-011](ADR-011-clarification-first-llm-routing-and-progressive-search.md) | Clarification + LLM route |

## Net mimari prensip

```text
LLM          → kullanıcıyı anlamaya yardım eder
Katalog      → gerçek ürünleri sağlar
Rule engine  → uygunluğu belirler
Hesap motoru → taksitleri hesaplar
Ranking      → sıralar
Validator    → gösterilecek iddiaları kontrol eder
Composer     → yalnız doğrulanmış gerçekleri kullanıcıya sunar
```

Yanlış mimari:

```text
Backend verileri → LLM → serbest metin cevap → kullanıcı
```

Doğru mimari:

```text
Backend doğrulanmış fact listesi
  → Response Fact Validator
  → Deterministic Response Composer
  → opsiyonel LLM açıklaması (allowed_facts ile sınırlı)
  → Final Claim Validator
  → kullanıcı
```

## Kararlar

1. **No evidence → no claim.** Kaynağı olmayan finansal veya ürün alanı
   kesin bilgi gibi gösterilemez.
2. Confidence **alan bazlıdır**; bir alanın yüksek olması diğerlerini
   güvenilir kılmaz.
3. Her kart alanı bir **doğruluk statüsü** taşır (backend zorunlu; UI
   sadeleştirilebilir).
4. LLM doğrudan son cevap yazmaz; yalnız `summary` /
   `comparison_explanation` / `clarification_question` üretebilir ve
   `allowed_facts` dışına çıkamaz.
5. Final Claim Validator **deterministiktir**; ikinci LLM ile kontrol
   yoktur.
6. Kaynak çelişkisi sessiz seçimle çözülmez; `CONFLICTED` + politika.
7. Source precedence matrisi DB policy’den yönetilir.
8. `AVAILABLE` ≠ `RULE_ELIGIBLE` ≠ `PERSONAL_APPROVAL_REQUIRED`.
9. Finansal hesap motoru LLM’den tamamen bağımsızdır.
10. `ZERO_RATE` ≠ `ZERO_TOTAL_COST`; masraf varsa “masrafsız” denmez.
11. Kampanya/fiyat **exact product offer / variant** üzerine bağlanır;
    benzer isimle merge yok.
12. Düşük güvenli görsel primary olamaz
    (`MEDIA_PRODUCT_MATCH_UNCERTAIN`).
13. “En uygun” etiketi zorunlu kapılardan geçer; aksi halde “en yakın
    seçenek”. Üç kazanan ayrı gösterilir (fiyat / aylık / toplam).
14. Üst sıra ürünler **reason_codes** ile gelir; “neden?” cevabı bunlardan
    üretilir.
15. Negatif tercihler kilitlenir; LLM inference kullanıcı düzeltmesini
    geçersiz kılamaz.
16. Merchant/kampanya metni **untrusted**; prompt injection sınırlıdır.
17. Schema drift / anomali → `QUARANTINED` / `SOURCE_SCHEMA_CHANGED`.
18. Üç cevap tipi: `ANSWERED` / `PARTIALLY_ANSWERED` / `CANNOT_VERIFY`.
19. Production hataları anonimleştirilerek golden/regression set’e eklenir.
20. Metamorphic testler zorunludur.
21. Shadow mode + merchant READY/PARTIAL/BLOCKED açılışı.
22. Kullanıcı geri bildirimi sonuç snapshot’ına bağlanır.
23. Hata sınıfları ayrılır; tek `WRONG_ANSWER` yoktur.
24. Quality circuit breaker kaynak bazlıdır; tüm chatbot kapanmak
    zorunda değildir.
25. Sponsorlu sıralama organik “en uygun” ile karışmaz.

---

# Katmanlar

## 1. Kanıt yoksa cevap yok (SOURCE_PROVENANCE_GATE)

Chatbot’un gösterdiği her kritik bilgi bir kaynak kaydına bağlanır:

| Alan | Kanıt |
|---|---|
| Ürün fiyatı | `price_snapshot_id` |
| Stok | `stock_snapshot_id` |
| Banka anlaşması | `merchant_finance_agreement_id` |
| Kampanya | `campaign_version_id` |
| Oran | `rate_snapshot_id` |
| Aylık ödeme | `payment_calculation_id` |
| Ürün özelliği | `product_attribute_source_id` |

Backend validator:

```text
No evidence → no claim
```

Örnek:

```text
YANLIŞ: Bu ürün Kuveyt Türk ile 12 ay alınabilir.
DOĞRU:  12 ay seçeneğine ilişkin güncel doğrulanmış kayıt bulunamadı.
        Mevcut doğrulanmış seçenekleri gösterebilirim.
```

## 2. Alan bazlı confidence

Yasak:

```json
{ "overall_confidence": 0.93 }
```

Zorunlu:

```json
{
  "confidence": {
    "intent": 0.98,
    "merchant": 0.95,
    "category": 0.99,
    "brand": 0.82,
    "institution": 0.61,
    "budget": 1.0,
    "term": 1.0
  }
}
```

Karar alan bazında:

- Merchant yüksek → Teknosa kullanılabilir
- Institution düşük → otomatik seçilmez → clarification

Eşikler ADR-010/011 DB policy ile uyumlu kalır; alanlar birbirini
kurtarmaz.

## 3. Doğruluk statüsü (field truth status)

Ürün kartı alanları (backend):

```text
VERIFIED
SOURCE_PROVIDED
CALCULATED
CALCULATED_ESTIMATE
INFERRED
STALE
CONFLICTED
UNAVAILABLE
```

Örnek:

```text
Satış fiyatı: 42.999 TL — VERIFIED — son kontrol 8 dk önce
Aylık ödeme: 4.281 TL — CALCULATED_ESTIMATE — rate_snapshot_123
Stok: UNAVAILABLE
```

UI teknik etiketleri göstermek zorunda değildir; backend farkı bilmek
zorundadır.

Özellikle karıştırılmaz:

```text
Kaynağın verdiği taksit
  ≠ Bizim hesapladığımız tahmini taksit
  ≠ Kullanıcıya özel banka teklifi
```

## 4. LLM doğrudan son cevap yazmaz

LLM yazabilir:

```json
{
  "summary": "...",
  "comparison_explanation": "...",
  "clarification_question": "..."
}
```

LLM girdisi `allowed_facts` ile sınırlıdır:

```json
{
  "allowed_facts": [
    { "fact_id": "fact_1", "type": "PRICE", "value": "42999 TRY" },
    { "fact_id": "fact_2", "type": "MONTHLY_PAYMENT", "value": "4281 TRY" },
    { "fact_id": "fact_3", "type": "TERM", "value": "12 months" }
  ]
}
```

LLM **yapamaz**: yeni banka, tutar, vade, ürün özelliği, kampanya şartı.

Modüller (hedef):

```text
src/taksitlio/answer_integrity/
  facts.py              # FactEnvelope, allowed_facts builder
  response_composer.py  # Deterministic Response Composer
  claim_validator.py    # Final Claim Validator
  truth_status.py       # field truth statuses
  conflict.py           # SOURCE_CONFLICT_GATE
```

## 5. Final Claim Validator (CLAIM_GROUNDING_GATE)

İkinci LLM ile kontrol **yoktur**. Deterministik kontroller:

- Cevaptaki tüm para tutarları `allowed_facts` içinde mi?
- Tüm banka isimleri sonuç setinde mi?
- Vadeler doğrulanmış mı?
- “En uygun” denilen ürün ranking kazananı mı?
- “Faizsiz” → `rate_type = ZERO_RATE` mi? (ve masraf iddiası ayrı)
- “Stokta” → stock status `AVAILABLE` mi?

Başarısızlık:

```text
CLAIM_VALIDATION_FAILED
  → LLM metni kullanıcıya gönderilmez
  → deterministic template kullanılır
```

## 6. Kaynak çelişkisi (SOURCE_CONFLICT_GATE)

Örnek: banka sayfası 12 ay, merchant sayfası 9 ay.

```text
Durum: CONFLICTED
```

Çözüm politikası (sıra):

1. Kaynak önceliği (precedence matrisi)
2. Kaynak güncelliği
3. Anlaşma kapsamı
4. Merchant / category / product specificity

Çözülemiyorsa kullanıcıya dürüst açıklama; çelişkili kayıt **asla**
“en uygun teklif” olamaz.

## 7. Source precedence matrisi

DB policy’den yönetilir (kodda hardcode yok):

| Veri türü | Öncelik |
|---|---|
| Teknik özellik | Üretici → merchant feed → merchant sayfası → enrichment |
| Fiyat | Merchant API/feed → merchant ürün sayfası |
| Stok | Merchant API/feed → merchant ürün sayfası |
| Banka kampanyası | Banka API/resmî → merchant anlaşma kaydı |
| Merchant–banka anlaşması | Taksitlio doğrulanmış → merchant/banka kaynağı |
| Aylık ödeme | Kaynak planı → deterministik hesap |
| Ürün görseli | Merchant/üretici doğrulanmış görsel |

Tablo (hedef): `source_precedence_policies` (+ version).

## 8. Eligibility / availability / approval ayrımı

| Kavram | Anlam |
|---|---|
| `AVAILABLE` | Finansman ürünü platformda ve merchant’ta mevcut |
| `RULE_ELIGIBLE` | Ürün, tutar, kategori, vade genel kuralları karşılıyor |
| `PERSONAL_APPROVAL_REQUIRED` | Kişisel değerlendirme bankaya ait (ADR-009 Campaign Gate) |

İzinli konuşma:

```text
Bu ürün için 12 ay finansman seçeneği mevcut görünüyor.
Nihai limit ve onay finans kuruluşunun değerlendirmesine bağlıdır.
```

Yasak:

```text
Bu ürünü 12 ay taksitle alabilirsiniz.
```

## 9. Finansal hesap motoru (PAYMENT_CALCULATION_GATE)

LLM hiçbir finansal hesap yapmaz.

Zorunlu:

- Decimal arithmetic
- Sabit rounding policy
- Rate type kontrolü
- Ücret / masraf / peşinat / ertelenmiş ilk ödeme / balon / sigorta /
  vergi / fonlar
- Her banka ürünü için `calculation_method_version`

Kontroller:

- Taksitler toplamı = toplam geri ödeme mi?
- Toplam geri ödeme = anapara + maliyet + ücretler mi?
- Kaynak plan ↔ bizim hesap farkı tolerans içinde mi?
- Son taksit yuvarlama farkı doğru mu?

Aşım:

```text
PAYMENT_PLAN_RECONCILIATION_FAILED → ödeme planı gösterilmez
```

ADR-010 `payment_plan` modülü bu gate’e bağlanır; uydurma oran yok kuralı
korunur.

## 10. “Faizsiz” özel koruma

```text
ZERO_RATE        ≠  ZERO_TOTAL_COST
```

İzinli:

```text
%0 oranlı finansman
Toplam ek masraf: 750 TL
```

Masraf varken “masrafsız” etiketi **yasak**.

## 11. Ürün kimliği (PRODUCT_IDENTITY_GATE)

Varyantlar (RAM / depolama / renk / model yılı) sessiz birleştirilmez.

Canonical eşleştirme sırası (ADR-010 ile uyumlu):

```text
EAN/GTIN → MPN → marka + tam model → varyant özellikleri
```

Banka kampanyası ve fiyat:

```text
canonical product  değil
exact product offer / variant  üzerine bağlanır
```

## 12. Görsel–ürün uyumu

Kontroller: varyant eşleşmesi, renk, model ailesi, paket vs ürün,
kategori görseli, merchant logosu ürün görseli mi?

```text
MEDIA_PRODUCT_MATCH_UNCERTAIN → primary image olamaz
```

Yanlış görsel, görsel göstermemekten daha kötü kabul edilir.
ADR-010 `IMAGE_UNAVAILABLE` ile uyumlu.

## 13. “En uygun” kapısı (RECOMMENDATION_INTEGRITY_GATE)

Zorunlu koşullar (hepsi):

- ≥3 karşılaştırılabilir aday
- Fiyatlar fresh
- Stok doğrulanmış
- Varyantlar karşılaştırılabilir
- Toplam geri ödeme mevcut
- Bankacılık mapping doğrulanmış
- Kampanya aktif
- Kritik özellikler eksik değil

Eksikse etiket: **Kriterlerinize en yakın seçenek** (ADR-010 §69–75 ile
uyumlu, sıkılaştırılmış).

Üç ayrı kazanan:

```text
En düşük satış fiyatı
En düşük aylık ödeme
En düşük toplam geri ödeme
```

## 14. Recommendation explanation

```json
{
  "rank": 1,
  "reason_codes": [
    "REQUIRED_ATTRIBUTES_MATCHED",
    "WITHIN_BUDGET",
    "LOWEST_TOTAL_REPAYMENT",
    "STOCK_VERIFIED",
    "FRESH_PRICE"
  ]
}
```

“Neden?” cevabı LLM tahminiyle değil reason code → deterministic
template ile üretilir. LLM yalnız `allowed_facts` + reason code
açıklamasını süsleyebilir; yeni reason uyduramaz.

## 15. Negatif tercihler (NEGATIVE_CONSTRAINT_GATE)

```text
USER_CORRECTION
  > USER_EXPLICIT
  > CLARIFICATION_ANSWER
  > DETERMINISTIC_PARSE
  > LLM_INFERENCE
```

LLM inference kullanıcı açık talebini / dışlamasını geçersiz kılamaz.
ADR-007 negation/correction hattı bu gate’e bağlanır.

## 16. Prompt injection (PROMPT_INJECTION_GATE)

Untrusted:

```text
Merchant HTML
Campaign text
Product description
```

Akış:

```text
sanitize → extract fields → data boundary
  → LLM’e yalnız quoted / untrusted content olarak
```

Sistem talimatları ile source text kesin ayrılır. Injection ranking veya
eligibility değiştiremez.

## 17. Schema drift / anomali (SCHEMA_DRIFT_GATE)

Örnek sinyaller:

- Fiyat bir çalışmada %90+ düştü
- Ürün sayısı %80 azaldı
- Hepsi stok dışı
- Görsel sayısı sıfır
- Kampanya sayısı beklenmedik arttı
- Vade 12 → 120
- Para birimi değişti

Sonuç:

```text
QUARANTINED
SOURCE_SCHEMA_CHANGED
```

Eski doğrulanmış kayıt TTL içinde korunabilir; chatbot’a ham anomali
yayılmaz (ADR-010 quarantine ile uyumlu).

## 18. Eksik bilgi dürüstlüğü

Yasak: “Muhtemelen stokta”, “Büyük ihtimalle 12 ay vardır.”

Cevap tipi:

```text
ANSWERED
PARTIALLY_ANSWERED
CANNOT_VERIFY
```

## 19. Golden Dataset (gerçek sorgular)

Yalnız geliştirici cümleleri yetmez. Gruplar:

- Yazım hataları, Türkçe ekler, uzun mesajlar
- Negation, correction, fikir değiştirme
- Bütçe + aylık ödeme birlikte
- Çoklu banka / merchant
- Eksik bilgi, çelişkili istek
- Model kodları, yanlış yazılan marka/model

Örnekler:

```text
Teknoksa’dan laptob bakıyorum
Telefon değil tablet
12 ay demiştim ama 9 ay da olabilir
Kuveyt değil Yapı Kredi olsun
40 bin bütçem var ama aylık 3 bini geçmesin
```

Her production hatası anonimleştirilerek regression set’e eklenir
(ADR-005 genişletmesi).

## 20. Metamorphic testler

Aynı anlam → aynı aktif kategori / dışlama:

```text
Telefon istemiyorum, laptop göster.
Laptop göster, telefon olmasın.
Telefonu boşver, bilgisayar bakalım.
Cep telefonu değil dizüstü arıyorum.
```

İlgisiz cümle kararı bozmamalı:

```text
Bugün hava çok sıcak. Telefon istemiyorum, laptop göster.
```

## 21. Shadow mode ve merchant açılışı

```text
Yeni sistem sonucu üretir
  → kullanıcıya göstermez
  → mevcut sistemle karşılaştırılır
  → farklar raporlanır
```

Merchant (ADR-010):

```text
READY   → aç
PARTIAL → yalnız doğrulanmış alanlar
BLOCKED → chatbot sonucuna alma
```

Tek source hatası tüm sistemi bozmamalı.

## 22. Geri bildirim → sonuç snapshot

“Bu sonuç yanlış” ile birlikte saklanır:

```text
query_version
parsed_constraints
catalog_revision
price_snapshot
campaign_snapshot
selected_product
selected_bank
response_fact_ids
```

Amaç: parser / mapping / stale / ranking / LLM explanation ayrımı.

## 23. Hata sınıflandırması

```text
QUERY_UNDERSTANDING_ERROR
ENTITY_RESOLUTION_ERROR
PRODUCT_IDENTITY_ERROR
STALE_PRICE_ERROR
STOCK_ERROR
BANK_MAPPING_ERROR
CAMPAIGN_MAPPING_ERROR
PAYMENT_CALCULATION_ERROR
RANKING_ERROR
LLM_EXPLANATION_ERROR
UI_DISPLAY_ERROR
SOURCE_DATA_ERROR
```

Her sınıfın sahibi ve metriği ayrıdır. Tek `WRONG_ANSWER` yok.

## 24. Quality circuit breaker

Kaynak bazlı otomatik kısıtlama:

| Sinyal | Aksiyon |
|---|---|
| Merchant A broken price rate > %5 | price results disabled |
| Bank B campaign mismatch > 0 | campaign results disabled |
| Image source broken > %3 | fallback source |

Tüm chatbot’un kapanması gerekmez.

## 25. Ticari sıralama şeffaflığı

```text
Organik en uygun  ≠  Sponsorlu sonuç
```

Sponsorlu ağırlık:

- Zorunlu kullanıcı kriterlerini aşamaz
- Uygun olmayan ürünü yukarı taşıyamaz
- Stale fiyatı öne çıkaramaz
- “En uygun” etiketi alamaz

---

# Quality gates (yeni)

| Gate | Anlam |
|---|---|
| `SOURCE_PROVENANCE_GATE` | Her kritik alan kanıt ID’sine bağlı |
| `CLAIM_GROUNDING_GATE` | Allowed facts dışı claim yok |
| `PAYMENT_CALCULATION_GATE` | Hesap reconciliation + versioned method |
| `PRODUCT_IDENTITY_GATE` | Exact offer/variant; yanlış merge = 0 |
| `FINANCE_MAPPING_GATE` | ADR-010 ile sıkı; yanlış banka/kampanya = 0 |
| `RECOMMENDATION_INTEGRITY_GATE` | “En uygun” zorunlu koşulları |
| `NEGATIVE_CONSTRAINT_GATE` | Kullanıcı dışlaması geri gelmez |
| `SOURCE_CONFLICT_GATE` | CONFLICTED sessiz seçilmez |
| `SCHEMA_DRIFT_GATE` | Anomali → quarantine |
| `PROMPT_INJECTION_GATE` | Untrusted text ranking’i değiştiremez |

ADR-010 gate’leri (`Data Quality`, `Recommendation`, `Finance Mapping`)
bu gate’lerle **sıkılaştırılır**; çelişki halinde bu ADR kazanır.

## Kritik kabul koşulları (sıfır tolerans)

| Koşul | Hedef |
|---|---|
| Kaynağı olmayan finansal claim | 0 |
| Yanlış banka mapping | 0 |
| Yanlış kampanya mapping | 0 |
| Yanlış aylık ödeme | 0 |
| Stale fiyatın güncel gösterilmesi | 0 |
| Yanlış ürün varyantı eşleşmesi | 0 |
| Kullanıcı dışlamasının geri gelmesi | 0 |
| LLM’in uydurduğu sayısal değer | 0 |
| Prompt injection ile ranking değişmesi | 0 |

## Response outcome tipleri

```text
ANSWERED
PARTIALLY_ANSWERED
CANNOT_VERIFY
CLAIM_VALIDATION_FAILED   # internal; kullanıcıya template
PAYMENT_PLAN_RECONCILIATION_FAILED
```

## Migration / modül hedefleri (P0+; kabul sonrası)

Önerilen paketler:

```text
src/taksitlio/answer_integrity/
src/taksitlio/claim_validation/
src/taksitlio/recommendation_safety/
```

Önerilen migration (sıradaki uygun V0xx):

- `response_facts` / `response_fact_links`
- `field_truth_status` projections (veya card envelope alanları)
- `source_precedence_policies`
- `quality_circuit_breakers`
- `feedback_result_snapshots`
- `error_class_events`
- `shadow_mode_comparisons`

Mevcut ADR-010/011 tabloları silinmez; fact ID’leri mevcut snapshot /
campaign_version / payment_calculation satırlarına FK ile bağlanır.

## Uygulama sırası (kabul sonrası)

1. Fact envelope + provenance validator (no evidence → no claim)
2. Field truth status + card composer wiring
3. Deterministic Response Composer
4. Final Claim Validator + template fallback
5. Field-level confidence karar politikası
6. Source conflict + precedence policy
7. Payment reconciliation gate (mevcut `payment_plan` üzerine)
8. ZERO_RATE / ZERO_TOTAL_COST ayrımı
9. Product identity / media match gates
10. Recommendation integrity + reason_codes
11. Negative constraint lock + injection boundary
12. Schema drift / circuit breaker
13. Golden + metamorphic suites
14. Shadow mode + feedback snapshots + error classes
15. Sponsored ranking isolation (ticari ihtiyaç doğunca)

## Yapılmayacaklar

- LLM’in serbest metinle son kullanıcı cevabı yazması
- İkinci LLM ile claim validation
- Tek `overall_confidence` ile auto-select
- Kanıtsız “faizsiz / stokta / en uygun” iddiası
- Çelişkili kaydı sessiz seçme
- Kişisel onay iddiası (ADR-009 Campaign Gate kapalıyken)
- Threshold düşürerek sıfır-tolerans metriklerini “geçme”

## Test planı

- Unit: claim validator (tutar / banka / vade / stok / faizsiz)
- Unit: payment reconciliation fail → plan gizlenir
- Unit: negative constraint lock
- Unit: untrusted text injection ranking’i değiştirmez
- Integration: composer → optional LLM → claim fail → template
- Golden + metamorphic suites (ADR-005 genişletmesi)
- Shadow: yeni composer vs mevcut GroundedResponseGenerator

## Sonuçlar

- ADR-010/011 ürün aramasını çalıştırır; bu ADR kurumsal cevap
  güvenilirliğini bağlar.
- LLM anlamaya yardım eder; iddia üretmez.
- Sıfır-tolerans finansal claim’ler gate’lerle ölçülür.
- Kod P0’ı land edildi; sıfır-tolerans gate testleri zorunlu kalır.
- P1+: shadow/feedback API wiring, golden/metamorphic genişletme, DB policy loaders.

# ADR-010: Real Product Catalog, Campaigns, and Fast Offer Comparison (V2)

## Durum

**Accepted — design + P0 skeleton (2026-07-31).**

Bu ADR, gerçek merchant/ürün/görsel/fiyat/stok ve banka kampanya
verisiyle LLM’siz hızlı teklif karşılaştırma yolunu tanımlar. V004
`campaigns` (ürün alanları campaign satırında) **legacy** kabul edilir;
yeni model yan yana büyür.

Kişiselleştirilmiş kredi onayı / kesin limit önerisi ADR-009
`Campaign Gate` geçilene kadar **aktif edilmez** (§76). Gerçek katalog,
deterministic karşılaştırma ve tahmini ödeme planı geliştirilebilir.

## Bağlam

ADR-009 sonrası:

| Gate | Durum |
|---|---|
| Safety | PASS (baseline) |
| Quality | QUALITY_READY (baseline; gerçek FAST HR100 REJECT) |
| Runtime | BLOCKED / PERFORMANCE_REJECT (CPU) |
| Provisional | not locked |
| Campaign (kişisel onay) | CLOSED |

V004’te `merchants` + `campaigns` vardır; ayrı `products`,
`financial_institutions`, ingestion ve media tabloları yoktur. Production
uygulamasında demo ürün, sahte fiyat/stok, varsayımsal banka anlaşması
veya uydurma kampanya bulunmayacaktır. Test fixture’ları yalnız otomatik
test ortamında kullanılabilir.

FAST A/B/C genel modeller HR100 hybrid kalite barını geçemedi; ürün
sorgusu yolu **LLM kullanmadan** structured + dinamik fuzzy resolution
ile çözülmelidir.

## Kararlar

1. Production’da yalnız gerçek kaynaklardan alınan merchant, ürün, görsel,
   fiyat, stok, banka, anlaşma, kampanya, vade, oran, ücret ve ödeme
   planı verisi kullanılır.
2. Merchant / banka / marka / kategori adına **static typo mapping
   yazılmaz**; tüm eşleşmeler dinamik katalog + fuzzy resolution ile
   yapılır. Confidence eşikleri DB policy’den yönetilir.
3. V004 product-centric `campaigns` modeli legacy’dir. Hedef model:
   `products` + `canonical_products` + `product_offers` +
   `finance_campaigns` + `product_finance_options` projections.
4. Merchant merkezî katalog bir kez ingest edilir; 12.500 şube ayrı ayrı
   taranmaz. Şubeler `merchant_locations` olarak tutulur.
5. Her merchant için ayrı adapter; tek genel scraper yoktur. Capability
   registry zorunludur. API/feed varsa HTML crawler kullanılmaz.
6. Credential’lar DB’ye açık yazılmaz; `credential_ref` secret manager
   referansıdır.
7. Merchant görselleri hotlink edilmez; media pipeline → object storage →
   CDN. Primary yoksa `IMAGE_UNAVAILABLE`; uydurma görsel yok.
8. Fiyat/stok overwrite edilmez; snapshot + history tutulur. Freshness
   etiketleri: `FRESH` / `STALE` / `EXPIRED` / `UNVERIFIED` /
   `SOURCE_UNAVAILABLE`.
9. Ödeme planı iki tiptir: `CALCULATED_ESTIMATE` (“Tahmini aylık ödeme”)
   ve `SOURCE_PROVIDED_OFFER`. Kullanıcıya özel onay alınmadan
   “Kesin aylık taksitiniz” denmez.
10. Ranking’de merchant/banka/kategori adına hardcode yok; ağırlıklar DB
    policy’den gelir. Stale / unknown stock / expired campaign “en uygun”
    olamaz.
11. Chatbot crawler’a senkron bağlanmaz; progressive response +
    precomputed projections kullanılır.
12. Kişiselleştirilmiş Campaign Gate ADR-009’a bağlı kalır. Bu ADR gerçek
    kampanya kataloğu + deterministic comparison’a izin verir; kesin
    kredi onayı vermez.

---

# V2 — Gerçek ürün verisi, görsel kataloğu, banka kampanyaları

## 31. Ana hedef

Örnek sorgu LLM olmadan çözülür:

```text
Teknoksa’dan 45 bin liraya kadar 16 GB RAM laptop istiyorum.
Kuveyt Türk veya Yapı Kredi ile 12 ay taksit varsa göster.
```

```text
teknoksa → fuzzy merchant resolution → Teknosa
45 bin liraya kadar → total_price <= 45.000 TRY
16 GB RAM → ram_gb >= 16
laptop → dynamic category resolution
Kuveyt Türk / Yapı Kredi → dynamic financial institution resolution
12 ay → requested_term = 12
```

Sonuç kartında: gerçek görsel, ad, marka/model, merchant, fiyat, stok,
uygun bankalar/vadeler, aylık ve toplam ödeme, ücretler, kampanya bitişi,
ürün URL, son güncelleme, en uygun etiket.

## 32. Statik typo ve alias yasağı

Production’da `if query == "teknoksa"` benzeri kod yok. Akış:

```text
token → Unicode/TR normalize → exact canonical → exact alias
→ normalized token-set → pg_trgm → char n-gram → edit-distance
→ confidence → auto-select / clarification
```

Başlangıç confidence (DB policy):

```text
>= 0.92 → auto
0.78–0.92 → doğrulama sorusu
< 0.78 → çoklu aday veya LLM route
```

Yakın adaylarda otomatik seçim yok.

## 33–35. Veri edinme ve ingestion modeli

Öncelik: Merchant API → feed → affiliate → sitemap/JSON-LD → product
pages → kontrollü browser crawler.

Adapter:

```text
MerchantProductSourceAdapter
  discover_products / fetch_product / fetch_offers
  fetch_stock / fetch_media / fetch_finance_metadata
```

Capabilities: `PRODUCT_DISCOVERY`, `PRODUCT_DETAIL`, `PRICE`, `STOCK`,
`MEDIA`, `CATEGORY`, `ATTRIBUTE`, `CAMPAIGN`, `FINANCE_OPTION`,
`BRANCH_AVAILABILITY`.

Tablolar: `ingestion_sources`, `ingestion_source_capabilities`,
`ingestion_source_credentials`, `ingestion_runs`, `ingestion_run_items`,
`ingestion_failures`, `source_rate_limits`, `source_health_status`.

## 34. Merchant / location

```text
Merchant → merkezi ürün kataloğu
Merchant location / şube → şehir, adres, aktiflik, şube stok/finans
```

Aynı ürün binlerce şube için kopyalanmaz.

## 36–37. Ürün ve özellik modeli

`products` (merchant SKU, GTIN/EAN/MPN, brand, model, descriptions,
source_url, seen/verified timestamps) + `canonical_products`.

Canonical eşleştirme: GTIN/EAN → MPN → marka+model → güvenli signature.
Düşük güvende merge yok.

Özellikler kategoriye özel kolon değil:
`attribute_definitions`, `attribute_units`, `category_attribute_links`,
`product_attribute_values` (raw/normalized/unit/source/confidence).

## 38–40. Medya

Hotlink yok. Download → content-type/size → decode → perceptual hash →
duplicate → quality → object storage → thumbnail → CDN.

Tablolar: `media_assets`, `product_media_links`, `media_variants`,
`media_ingestion_runs`, `media_quality_results`.

Primary görsel hedefleri: min 600×600, preferred ≥1000, aspect 0.75–1.33.
Varyantlar: 320/640/1200 WebP + orijinal. Chatbot ilk payload’da 320/640.

Primary yoksa `IMAGE_UNAVAILABLE`.

## 41–43. Offer, freshness, delta sync

`product_offers`, `product_offer_snapshots`, `product_price_history`,
`product_stock_snapshots`.

TTL (policy): price/stock 15–60 dk; product details 24s; images 7g
doğrulama; campaign/bank terms 30–60 dk.

Delta: source/item/content hash, ETag, Last-Modified; unchanged skip.

## 44–50. Banka, kampanya, oran, ödeme planı, projection

- `financial_institution_media` (logo CDN; yoksa metin)
- Modül: `src/taksitlio/campaign_catalog/`
- Tablolar: `finance_campaigns`, versions, merchants/categories/brands/
  products/channels/terms/exclusions/media/source_snapshots
- `finance_rate_snapshots`, tiers, fee snapshots — oran yoksa uydurma yok
- Modül: `src/taksitlio/payment_plan/` — calculations + installments
- Projection: `product_finance_options` (incremental rebuild)

## 51–57. Hızlı sorgu, progressive UI, ranking

Precompute: merchant/institution/brand/category indexes, product search
doc, attribute projection, active finance + best offer projections.
Redis alias/popular/best-offer cache (source of truth değil).

Performans (hedef): first response P95 &lt; 300 ms; complete &lt; 600 ms.

Progressive: 0–100 ms “arıyorum” → 100–300 ms kartlar → 300–600 ms
finans → lazy detail.

Sıralama modları: `CHEAPEST_PRODUCT_PRICE`, `LOWEST_MONTHLY_PAYMENT`,
`LOWEST_TOTAL_REPAYMENT`, `LONGEST_TERM`, `BEST_ATTRIBUTE_MATCH`,
`BEST_OVERALL_VALUE` (varsayılan).

En uygun güvenlik: stock AVAILABLE, price FRESH, image AVAILABLE,
finance/campaign ACTIVE, rate FRESH, agreement ACTIVE, ELIGIBLE.

Canonical product ile merchant/banka karşılaştırması.

## 58–62. Arama kalitesi, data quality, provenance

Entity resolution precision hedefleri (merchant ≥0.98, institution ≥0.99,
brand ≥0.98, category ≥0.95, false auto-resolution = 0).

`product_data_quality_score` → READY / PARTIAL / QUARANTINED / REJECTED.
QUARANTINED chatbot’ta gösterilmez.

Her gösterilen alan için source_reference + retrieved_at zorunlu.

## 63–66. Scheduler, search-driven freshness, hata toleransı

Modül: `src/taksitlio/ingestion_scheduler/`. Kuyruklar: discovery,
detail, price/stock/media/campaign/rate refresh, retry.

Stale aramada senkron crawl bekleme yok; stale etiketi + background
refresh. Kritik expired teklif “güncel” gösterilmez.

Typed hatalar: `SOURCE_TIMEOUT`, `SOURCE_BLOCKED`,
`SOURCE_SCHEMA_CHANGED`, `PRODUCT_PARSE_FAILED`, `MEDIA_FETCH_FAILED`,
`CAMPAIGN_PARSE_FAILED`, `RATE_UNAVAILABLE`.

## 67–68. Admin ve API

Admin: source/adapter health, coverage, freshness, duplicate/canonical
review, quarantine, manual refresh (audit’li override).

Örnek endpoint’ler: products/{id}/media|offers|finance-options|
payment-plans; campaigns; product-query/search|resolve-entities;
admin ingestion/data-quality.

## 69–75. UX, performans, release / recommendation gates

Merchant production açılışı: verified + healthy source + coverage +
image/price freshness + bank/campaign mapping + payment calc tests.
Merchant gate: READY / PARTIAL / BLOCKED.

`BEST_OVERALL_VALUE` yalnız ≥3 karşılaştırılabilir aday + fresh prices +
complete repayment vb. varsa; aksi halde “en yakın seçenek”.

## 76. Kampanya aktivasyon ayrımı

Bu sprintte gerçek kampanya kataloğu, banka mapping, payment plan,
deterministic comparison geliştirilebilir. Kişiselleştirilmiş kredi
onayı: Campaign Gate + Runtime Gate + bank API/approval olmadan açılmaz.

## 77. Yapılmayacaklar

Sahte ürün/fiyat/görsel/oran/anlaşma; static typo mapping; kategoriye
özel production if/else; her sorguda tüm merchant crawl; her istekte tüm
banka API; stale kampanyayı aktif gösterme; hotlink görsel.

## 78. Uygulama sırası

1. Real merchant and source catalog  
2. Ingestion framework  
3. İlk gerçek merchant adapter  
4. Product canonicalization  
5. Product and offer storage  
6. Media pipeline + object storage/CDN  
7. Bank and campaign ingestion  
8. Merchant–bank–product mapping  
9. Payment plan calculator  
10. Product finance projection  
11. Fast fuzzy query path  
12. Deterministic recommendation ranking  
13. Chatbot product cards  
14. Freshness scheduler  
15. Data-quality gates  
16. Performance benchmark  
17. Merchant-by-merchant production activation  

İlk doğrulama: 1 feed/API + 1 HTML/JSON-LD + 1 store-only merchant
modeli; sonra adapter sayısı artar. Kodda merchant adı hardcode yok.

## 79. Testler

Fuzzy resolution (katalogdan; static mapping yok), product ingestion,
media, campaign eligibility, payment plan, recommendation safety.

## 80. Görev sonu raporu (ölçülecekler)

Gerçek merchant/source/ürün/canonical/offer/banka/campaign/mapping
sayıları; image/price/stock/finance/payment-plan coverage; ingestion
P50/P95 + success/delta-skip; search latency + fuzzy precision; chatbot
first-card/full-result/image; recommendation accuracy; gates:
Data Ingestion, Data Quality, Fast Product Path, Finance Mapping,
Recommendation, Campaign; kalan blocker’lar.

## Fazlar (P0–P5)

| Faz | Kapsam |
|---|---|
| P0 | ingestion_sources* + merchant_locations; adapter protocol/registry; package stubs |
| P1 | products / canonical / offers / snapshots; ilk gerçek adapter |
| P2 | media pipeline |
| P3 | finance_campaigns + rates + payment_plan |
| P4 | finance projection + fuzzy query + ranking API |
| P5 | chatbot cards + scheduler + merchant READY/PARTIAL/BLOCKED |

## Yeni ADR-010 gate’leri

| Gate | Anlam |
|---|---|
| Data Ingestion Gate | Source healthy + runs başarılı |
| Data Quality Gate | Coverage + quarantine kuralları |
| Fast Product Path Gate | Latency + fuzzy precision |
| Finance Mapping Gate | Bank/campaign mapping precision |
| Recommendation Gate | Ranking safety + accuracy |
| Campaign Gate | Kişisel onay (ADR-009); katalog ≠ onay |

## Sonuçlar

- Legacy V004/campaign path hemen silinmez.
- Production gerçek-veri kuralı bağlayıcıdır.
- P0 sahte seed üretmez; crawler/chatbot kartı/LoRA bu ADR P0 kapsamında değildir.

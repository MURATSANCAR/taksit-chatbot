# ADR-013: Layered Verification and Release Gates

## Durum

**Accepted — design + Query Golden Set v1 bootstrap (2026-08-01).**

Bu ADR, gerçek ürün/finans chatbot yolunun tek bir “E2E deneme” ile
değil, bağımsız doğrulama katmanlarıyla açılmasını zorunlu kılar.
Parser doğru çalıştı diye ürün doğru değildir; ürün doğru diye banka
eşleşmesi doğru değildir; banka doğru diye taksit hesabı doğru değildir.

## Bağlam

ADR-010 / ADR-011 / ADR-012 iskeletleri ve sıfır-tolerans claim
kuralları kodda vardır. Eksik olan: ortam ayrımı, katmanlı gate
sözleşmesi ve ürün sorgusu odaklı versioned golden set.

ADR-005 kategori golden’ı semantic matcher içindir. Bu ADR’nin
**Taksitlio Query Golden Set v1** paketi ürün + merchant + banka +
fiyat/vade + route (FAST / CLARIFICATION / LLM) doğrular.

## Kararlar

1. Üç ortam zorunludur:
   - **TEST** — unit/integration; kontrollü fixture; kullanıcıya açık değil;
     chatbot ekranında demo ürün yok.
   - **STAGING** — gerçek merchant ürün/görsel/fiyat + gerçek banka/kampanya;
     test kullanıcıları.
   - **PRODUCTION** — yalnız bütün gate’leri geçen merchant’lar (`READY`).

2. Fixture yalnız kod davranışını test eder. Staging/production’da demo
   ürün gösterilmez (ADR-010 ile aynı).

3. Sekiz doğrulama katmanı bağımsız ölçülür; üst katman geçişi alt
   katmanı varsaymaz:

| Katman | Konu |
|---|---|
| L1 | Parser + entity resolution |
| L2 | Clarification-first (LLM kaçınma) |
| L3 | Gerçek ürün verisi (staging) |
| L4 | Banka / merchant mapping |
| L5 | Taksit / ödeme planı hesabı |
| L6 | “En uygun” / ranking integrity |
| L7 | LLM routing + claim yasakları |
| L8 | Progress truthfulness + logo |

Ek: performans, chaos, shadow mode (≥1000 anonim sorgu), insan UAT.

4. İlk paket: **Taksitlio Query Golden Set v1** — 1000 kullanıcı sorgusu.
   Aynı set dört lane’de çalışır: `parser` → `retrieval` → `finance` → `e2e`.
   Hata kaynağı (model / parser / veri / banka) lane farkından okunur.

5. Route alanları runtime ile uyumludur: `FAST` | `CLARIFICATION` | `LLM` |
   `DEGRADED` | `OUT_OF_SCOPE`. Dokümandaki `FAST_PATH_REQUIRED` =
   expected `route: "FAST"`.

6. Entity beklenenleri display_name / concept ile yazılır; production UUID
   golden’a gömülmez. Resolve TEST/STAGING katalogundan yapılır.

7. Static typo mapping yasaktır (ADR-010 §32). Kanıt: admin/katalogdan
   yeni merchant eklenir → yanlış yazılmış sorgu deploy olmadan fuzzy
   eşleşir. Kodda `teknoksa → Teknosa` yoktur.

8. Bootstrap (ADR-005 modeli): ~100 `HUMAN_REVIEWED` + ~900 `DRAFT`.
   Tam `ACCEPT` için HR büyümesi sonraki iştir; DRAFT-only set full
   ACCEPT alamaz.

## L1 — Parser gate (Query Golden v1)

Bucket hedefleri (toplam 1000):

| Bucket | N |
|--------|---|
| `fast_path` | 300 |
| `typo_fuzzy` | 200 |
| `negation_correction` | 150 |
| `clarification` | 150 |
| `llm_required` | 100 |
| `adversarial` | 100 |

Kabul (parser lane):

- Merchant precision ≥ 0.98
- Institution precision ≥ 0.99
- Category precision ≥ 0.95
- Price extraction ≥ 0.98
- Term extraction ≥ 0.98
- Negation recall ≥ 0.95
- Correction recall ≥ 0.90
- False auto-resolution = 0
- Açık sorguda gereksiz LLM = 0
- Clarification ile çözülecek sorguda LLM = 0

## L2–L8 (özet kabul)

- **L2:** mesaj başına ≤1 soru; arka arkaya ≤2; tekrar yok; önceki cevap
  unutulmaz; `llm_avoided_by_clarification_rate` başlangıç ≥ 0.60
- **L3:** yanlış ad/fiyat/URL/varyant = 0; bozuk görsel &lt; 1%; primary
  image ≥ 95%; güncel fiyat coverage ≥ 95%
- **L4:** yanlış banka/merchant mapping = 0; expired campaign = 0;
  excluded category sızıntısı = 0; inactive institution = 0
- **L5:** yanlış aylık/toplam/vade = 0; eksik oranla hesap = 0;
  kaynağı olmayan ücret = 0; tolerans dışı →
  `PAYMENT_PLAN_RECONCILIATION_FAILED` (gösterilmez)
- **L6:** cheapest / lowest monthly / lowest total = %100; yanlış
  “en uygun” = 0; &lt;3 adayda “en uygun” yerine “en yakın seçenek”
- **L7:** LLM ürün fiyatı / banka oranı / taksit / stok / kampanya tarihi /
  merchant anlaşması uyduramaz → `CLAIM_VALIDATION_FAILED`
- **L8:** sahte progress = 0; yanlış logo = 0; tamamlanmayan adıma ✓ = 0

## Sıfır-tolerans (release)

Yanlış banka/kampanya mapping, yanlış taksit, yanlış varyant, LLM
uydurma finans, expired kampanyanın aktif görünmesi, yanlış “en uygun”,
sahte progress mesajı — hepsi **0**.

## Release gate listesi

Parser → Entity Resolution → Clarification → Real Product Data →
Image Quality → Finance Mapping → Payment Calculation →
Recommendation Integrity → Progress Truthfulness → Performance →
Shadow Mode → UAT.

Merchant açılışı: 1 merchant → 1–2 kategori → doğrulanmış bankalar →
limited users; sonra genişleme. Merchant gate: `READY` / `PARTIAL` /
`BLOCKED`.

## Performans (hedef)

Fast parser P95 &lt; 30 ms; entity &lt; 50 ms; catalog lookup &lt; 50 ms;
retrieval &lt; 150 ms; finance &lt; 100 ms; fast-path total &lt; 300–500 ms;
ilk kart &lt; 500 ms; tam sonuç &lt; 1 s; partial LLM &lt; 4 s.

## Reddedilen alternatifler

- Tek chatbot denemesiyle “hazır” ilanı
- Fixture ürünü production/staging chatbot’a bağlamak
- Static typo map ile precision yükseltmek
- Aynı golden üzerinde continuous tuning + aynı set ile ACCEPT
- LLM ile taksit hesabı doğrulamak

## Sonuçlar

**Olumlu:** Katmanlı hata izolasyonu; ölçülebilir açılış kapıları;
data-driven fuzzy kanıtı.

**Risk:** 1000 satırın çoğu başlangıçta DRAFT; staging product/finance
golden’ları sonraki sprint. Parser lane TEST fixture ile çalışır;
retrieval/finance/e2e lane’leri staging bağlanana kadar stub kalabilir.

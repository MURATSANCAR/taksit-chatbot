# ADR-005: Türkçe Golden Set ve Semantic Evaluation

## Durum

Kabul edildi — MVP kalite kapısı (kampanya katmanından önce)

## Bağlam

Semantic matcher’ın “çalışıyor” olması yeterli değildir. Türkçe günlük konuşmada
MATCHED / AMBIGUOUS / NO_MATCH kararlarının doğruluğu, güvenliği (unsafe auto-select)
ve gecikmesi ölçülmeden kampanya katmanına geçilmemelidir.

Publish sözleşmesi (ADR-004 tamamlayıcı): embedding READY olmadan revision
PUBLISHED olamaz. Policy alanları canonical isimlerle tutarlı olmalıdır.

## Karar

1. Model/matcher seçimi yalnızca model self-description’a dayanmaz; golden evaluation zorunludur.
2. Türkçe golden dataset **versioned** ve sürüm bazında **immutable** tutulur.
3. Golden kayıtlar production kategori UUID/adına veya kod içi enum’a bağlı değildir;
   `fixture.*` stable key’ler runtime’da UUID’ye çözülür.
4. Evaluation izolasyonlu fixture katalog oluşturur (prepare → embed → READY_TO_PUBLISH → PUBLISH).
5. Beklenen sonuç türleri: EXACT_MATCH, ACCEPTABLE_SET, AMBIGUOUS, NO_MATCH, DEPENDENCY_FAILURE.
6. Split: development / validation / holdout. Holdout üzerinde tuning yasaktır.
7. Exact-match accuracy tek başına yeterli değildir; unsafe_auto_select_rate kritik metriktir.
8. Latency (P50/P95/P99), degraded mode, calibration ayrı ölçülür.
9. Ham utterance standart production log / standart evaluation raporuna yazılmaz (debug opt-in).
10. Kampanya geliştirmesine geçiş için açık ACCEPT/REJECT kalite kapısı zorunludur.
11. Semantic policy canonical alanlar: `minimum_candidate_score`, `minimum_auto_select_score`,
    `minimum_auto_select_gap`, `maximum_candidates`, ağırlıklar, degraded bayrakları.
    V008 legacy sütunları (`minimum_score`, `clarify_score_gap`) mapper ile okunur; destructive rename yok.
12. Policy challenger otomatik ACTIVE yapılmaz; AuditService + admin onayı gerekir.

## Reddedilen alternatifler

* Birkaç örnek cümleyle manuel test
* Model self-confidence’ı başarı metriği saymak
* Tüm AMBIGUOUS örnekleri yanlış kabul etmek
* Aynı dataset üzerinde sürekli tuning + aynı set ile ölçüm
* Kategori isimlerini evaluator koduna yazmak
* Yalnızca vector score ölçmek
* Yalnızca ortalama latency raporlamak
* Publish sonrası embedding üretmek (matcher’ı kısa süre embeddingsiz bırakmak)

## Sonuçlar

**Olumlu:** Ölçülebilir Türkçe kalite, güvenli auto-select disiplini, regression baseline.

**Risk:** 1.000 HUMAN_REVIEWED hedefi bu MVP bootstrap’ında tamamlanmaz; ≥250 sentetik/schema-valid
case ile altyapı kurulur, golden HUMAN_REVIEWED artışı sonraki iştir.

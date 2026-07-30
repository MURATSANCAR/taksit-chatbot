# ADR-006: Semantic Matcher Quality Hardening

## Durum

Kabul edildi — kalite sertleştirme sprinti (kampanya katmanı kapalı)

## Bağlam

Validation bootstrap REJECT: `unsafe_auto_select_rate=0` güvenli, ancak
`forbidden_candidate_violation_rate` düşük destekle şişiyor, `ambiguous_when_should_match`
yüksek ve retrieval/ranking/decision hataları ayrılmıyor. LexicalEmbedder latency
production sayılmamalı. Holdout tuning yasak.

## Karar

1. Kalite hata bucket’ları ve failure stage (RETRIEVAL / RANKING / DECISION) üzerinden geliştirilir.
2. Holdout üzerinde tuning yapılmaz.
3. Negatif kullanıcı tercihleri pozitif query embedding’ine karıştırılmaz; ayrı sinyaldir.
4. Explicit vs inferred exclusion ayrılır; model kategori ID/enum üretmez.
5. Candidate pool (`candidate_pool_size`) final Top-K (`maximum_candidates`)’den geniştir.
6. Metrikler pay, payda, support, Wilson CI ve LOW_SUPPORT gösterir; denominator=0 → NOT_APPLICABLE.
7. Gate violation **count** ve **rate**’i ayrı değerlendirir; DRAFT sentetik final ACCEPT üretemez.
8. Parent-child collapse policy ile yönetilir; sibling collapse edilmez.
9. Gerçek embedding yoksa typed `EMBEDDING_DEPLOYMENT_UNAVAILABLE`; LexicalEmbedder’a sessiz fallback yok.
10. Policy challenger validation üzerinde üretilir; otomatik ACTIVE yok.
11. Acceptance threshold’ları keyfî düşürülerek gate geçilmez.

## Reddedilen alternatifler

* Forbidden hatalarını görmezden gelmek
* Unsafe=0 diye matcher’ı kabul etmek
* Auto-select eşiğini doğrudan düşürmek
* Tüm ambiguity’yi büyük LLM’e göndermek
* Kategori listesini FAST promptuna koymak
* Negation’ı kategoriye keyword map etmek
* Holdout tuning
* LexicalEmbedder latency’yi production saymak

## Sonuçlar

**Olumlu:** Tanılı hatalar, negatif niyet, geniş pool, açıklanabilir metrikler.

**Risk:** HUMAN_REVIEWED yetersizken yalnızca PROVISIONAL_ACCEPT / INSUFFICIENT_REVIEWED_DATA.

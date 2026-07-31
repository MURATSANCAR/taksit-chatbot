# ADR-011: Clarification-First LLM Routing, Progressive Product Search, and Waiting Experience

## Durum

**Accepted — P0 skeleton (2026-07-31).**

Bu ADR, ADR-010 gerçek katalog / hızlı teklif yolunun devamıdır. LLM her
mesajda otomatik çalışmaz; önce deterministik parse + gap analysis +
clarification, gerekirse async LLM + progressive gerçek ara sonuçlar.

## Bağlam

ADR-010 sonrası:

| Gate | Durum |
|---|---|
| Fast Product Path | P14 (chat cards + guest UI) |
| Finance Mapping | P12 |
| Campaign (kişisel onay) | CLOSED |

Kullanıcı ürün sorgularında LLM çağrı oranı düşürülmeli; bekleme ekranı
yalnız gerçek backend event’leriyle ilerlemeli; sahte banka/merchant
logosu ve sahte progress yasaktır.

## Kararlar

1. LLM route yalnız soyut / çok boyutlu / clarification sonrası düşük
   confidence durumlarda açılır.
2. Clarification-first: tek kısa cevap HIGH confidence’a çıkarabiliyorsa
   LLM çağrılmaz. Mesaj başına ≤1 soru, oturumda arka arkaya ≤2.
3. Her ürün sorgusu bir `search_session` state machine’idir; query
   versioning immutable’dır.
4. Progress için SSE tercih edilir; her progress mesajı backend event +
   `data_origin` ile doğrulanır.
5. LLM yalnız yapılandırılmış semantic patch üretir; ürün/fiyat/oran/ID
   uyduramaz; validation + stale version koruması zorunludur.
6. LLM çalışırken deterministic partial retrieval yapılır; etiket
   «Ön sonuçlar». Timeout → `COMPLETED_DEGRADED`.
7. Logo rail yalnız gerçek aday event’lerinden gelir.
8. Timeout / queue süreleri config veya DB policy’den yönetilir; model
   adına özel süre yazılmaz. Frontend’e yalnız `UNDERSTANDING_SERVICE`
   rolü gider.

## Modüller

Backend: `search_sessions`, `query_understanding`, `query_clarification`,
`llm_routing`, `search_progress`, `progressive_results`, `query_state`,
`query_fallback`.

Frontend (mevcut `web/taksitlio`): `js/search-progress/`,
`js/clarification/`, `js/constraint-chips/`, `js/progressive-products/`,
`js/logo-progress-rail/`, `js/search-session/`.

## Migration

`V021__search_sessions_clarification_and_llm_jobs.sql`

## Gates (yeni)

- `CLARIFICATION_ROUTING_GATE`
- `PROGRESS_TRUTHFULNESS_GATE`
- `PROGRESSIVE_RESULT_GATE`
- `STALE_LLM_PROTECTION_GATE`
- `LLM_TIMEOUT_FALLBACK_GATE`
- `LOGO_CORRECTNESS_GATE`

## Yapılmayanlar (bu ADR)

Yeni model indirme, fine-tune, gerçek kredi başvurusu, kişiye özel limit,
sahte canlı banka bağlantısı, rastgele logo, sahte yüzde progress,
LLM’in fiyat/ödeme üretmesi.

Cevap bütünlüğü / claim grounding / recommendation safety katmanı
[ADR-012](ADR-012-answer-integrity-claim-grounding-and-recommendation-safety.md)
kapsamındadır.

## Remote understanding (P2)

`LlmUnderstandingWorker` OpenAI-compatible provider kullanır. Endpoint
önceliği: `UNDERSTANDING_*` → `FAST_C_*` (9B / `remote_nine_b`) →
`FAST_PROVIDER_*`. Env yoksa deterministic fallback. Frontend’e yalnız
`UNDERSTANDING_SERVICE` gider.

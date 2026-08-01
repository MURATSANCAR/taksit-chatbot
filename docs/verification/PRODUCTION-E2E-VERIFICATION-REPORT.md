# PRODUCTION E2E VERIFICATION REPORT

**Task:** TASK-E2E-PROD-VERIFY  
**Generated:** 2026-08-01  
**Decision:** **PRODUCTION_E2E_NOT_READY**

Artifacts: `artifacts/e2e-production-verification/`  
System map: `docs/verification/taksitlio-e2e-system-map.md`  
Runner: `scripts/run_production_e2e_verification.py` (production = read-only)

---

## 1. Yönetici özeti

Production kataloğunda gerçek ürünler vardır (**14.341** aktif ürün/offer). Fast parser, fuzzy entity resolution (static typo map yok), clarification, progress truthfulness ve claim/payment **unit/acceptance** katmanları çalışır. Ancak uçtan uca **PRODUCTION_E2E_READY** için zorunlu kanıtlar eksik veya başarısız:

- Kampanyalar `verification_status=UNVERIFIED`
- `payment_plan_calculations = 0` (kalıcı ödeme planı E2E yok)
- Production-ID bağlı retrieval golden / staging snapshot yok
- Görsel HTTP decode batch yok; primary image coverage **&lt; 95%**
- Browser E2E, live shadow ≥1000, human UAT yok
- Query Golden parser hâlâ **BOOTSTRAP** (DRAFT-heavy fixtures)

Bu nedenle nihai karar: **PRODUCTION_E2E_NOT_READY**.

---

## 2. Test edilen sistem kapsamı

| Alan | Kanıt |
|---|---|
| Repo discovery + system map | PASS |
| Production read-only inventory | PASS (`BEGIN TRANSACTION READ ONLY`) |
| Hardcode typo scan | PASS (static map yok) |
| Query Golden lanes (TEST fixture) | parser BOOTSTRAP; clarification/perf/finance/bank/chaos PASS |
| Entity resolution unit + fuzzy acceptance | PASS |
| Search session / progress / stale LLM acceptance | PASS |
| Payment calculator unit | PASS |
| Claim grounding unit/acceptance | PASS |
| Catalog readiness (count ≥ 1000) | PASS |

## 3. Test edilmeyen alanlar (NOT_VERIFIED)

| Alan | Neden |
|---|---|
| Staging snapshot tenant E2E | İzole staging DB bu koşuda bağlanmadı |
| Production-ID retrieval golden | Expected ID’ler snapshot revision’a bağlanmadı |
| Image HTTP reachability/decode | Batch probe çalıştırılmadı |
| Logo correctness on live cards | Browser/SSE live capture yok |
| Frontend Playwright E2E | Repo’da guest UI suite koşulmadı |
| Load 10–250 users | Çalıştırılmadı |
| Live shadow ≥1000 | Yalnız offline golden shadow |
| Human UAT ≥150 | Çalıştırılmadı |
| Live LLM timing / partial &lt;4s | Canlı endpoint ölçümü yok |
| Live SSE sequence capture | Yok |

---

## 4. Production snapshot bilgisi

Kaynak: `artifacts/e2e-production-verification/production-inventory.json`

| Field | Value |
|---|---|
| snapshot_id | `5939e3b8e5e7a686` |
| snapshot_created_at | 2026-07-31T23:43:17Z |
| catalog_revision | product_search_projection rebuilt_at `2026-07-31 23:39:30+00` |
| offer_revision | offers=14341 |
| finance_revision | agreements=4, finance_opts=27293, payment_plans=**0** |
| campaign_revision | campaigns_active=4 |

> Golden fixture sonuçları bu snapshot ID’lerine bağlı değildir → production ID golden olarak **geçerli sayılmamalıdır**.

---

## 5. Veri envanteri

| Metric | Count |
|---|---|
| Active products | 14,341 |
| Active merchants | 26 |
| Active brands | 301 |
| Active categories | 9 |
| Offers | 14,341 |
| Institutions | 4 |
| Financial products | 2 |
| Active agreements | 4 |
| Active campaigns | 4 |
| Rate snapshots | 7 |
| Payment plan calculations | **0** |
| Eligible finance options | 27,293 |
| READY media assets | 9,773 |
| Search projection rows | 14,341 |
| Entity index rows | 15,202 |

### Coverage

| Coverage | % / note |
|---|---|
| Primary image | **75.98%** (hedef ≥95% FAIL) |
| Valid product URL format | 100% |
| Fresh price | 100% |
| Stock known | 12.45% |
| Brand | 11.2% |
| Category | 0.45% |
| Attributes | 13.88% |
| Finance mapping (eligible opt) | 47.81% |
| Gallery | 0.88% |
| Payment-plan persistence | **0 rows** |

## 6. Veri kalite sonuçları

| Status (projection) | Count |
|---|---|
| READY | 1,119 |
| PARTIAL | 13,220 |
| REJECTED | 2 |

- Active expired campaigns: **0**
- Active campaigns missing `source_reference`: **0**
- Campaign `verification_status`: **all UNVERIFIED (5)** → CAMPAIGN_VALIDITY_GATE **FAIL**
- HTTP image decode: **NOT_VERIFIED**
- Source products not mutated in this verification run

---

## 7–11. Parser / entity / negation / clarification / retrieval

### Parser (Query Golden TEST fixture, n=1000)

| Metric | Value |
|---|---|
| Gate | **BOOTSTRAP** |
| Merchant / institution / category precision | 1.0 / 1.0 / 1.0 |
| Negation recall | 1.0 |
| Correction recall | 1.0 |
| False auto-resolution | **0** |
| Clarification accuracy (noisy DRAFT) | 0.36 |
| Unnecessary LLM on FAST | 0 |

Hardcode typo scan: **PASS** (no static `teknoksa→…` maps in `src/taksitlio`).

### Clarification

| Metric | Value |
|---|---|
| Gate | PASS |
| llm_avoided_by_clarification_rate | **1.0** (≥0.60) |
| unnecessary_llm_on_clarification | **0** |

### Product retrieval (production IDs)

**NOT_VERIFIED** — staging snapshot + manual expected candidate sets required.

Fixture `product_data` lane: PASS on TEST product golden only.

---

## 12. Görsel doğruluk

| Check | Result |
|---|---|
| Primary coverage | 75.98% → below 95% bar |
| HTTP status / content-type / decode | NOT_VERIFIED |
| Wrong product image = 0 | NOT_VERIFIED |
| Merchant sampling 50+/merchant | NOT_VERIFIED |

## 13–15. Finance / campaign / payment

| Check | Result |
|---|---|
| Bank mapping TEST golden | PASS (fixture) |
| Finance scenarios TEST | PASS (fixture) |
| Prod agreements active | 4 |
| Prod campaigns active | 4, all **UNVERIFIED** |
| Expired campaign displayed | 0 active-expired rows |
| Rate snapshots | 7 FRESH; source_reference present |
| Payment calculator unit | PASS (wrong monthly=0 in unit) |
| Prod `payment_plan_calculations` | **0** → persistence E2E NOT_VERIFIED |

## 16–17. Recommendation / claims

| Check | Result |
|---|---|
| Recommendation on prod snapshot | NOT_VERIFIED |
| Claim validator unit/acceptance | PASS |
| Live transcript claim extract | NOT_VERIFIED |

## 18–19. LLM / supersede

| Check | Result |
|---|---|
| Routing policy unit + golden | PARTIAL / BOOTSTRAP |
| Live LLM partial &lt;4s | NOT_VERIFIED |
| Stale LLM protection acceptance | PASS |
| Live supersede scenario | NOT_VERIFIED |

## 20–21. Progress / frontend

| Check | Result |
|---|---|
| Progress truthfulness acceptance | PASS |
| Forbidden “live bank API” phrases guarded | PASS (unit) |
| Live SSE sequence | NOT_VERIFIED |
| Playwright UI E2E | NOT_VERIFIED |

## 22–25. Perf / load / chaos / security / shadow / UAT

| Lane | Result |
|---|---|
| Parser/gap perf (fixture) | PASS — total route P95 **~2.87 ms** |
| Product retrieval P95 on proj | NOT_VERIFIED (smoke earlier ~100ms, not gated here) |
| Load test | NOT_VERIFIED |
| Chaos golden | PASS (fixture) |
| Security (injection unit) | PARTIAL |
| Shadow | BOOTSTRAP offline; live ≥1000 NOT_VERIFIED |
| UAT | NOT_VERIFIED |

---

## 26. Açık hatalar (severity)

### BLOCKER
1. `PAYMENT_CALCULATION_ERROR` — `payment_plan_calculations` empty on production  
2. `PRODUCT_RETRIEVAL_ERROR` — no production-ID retrieval golden on staging snapshot  

### CRITICAL
1. `CAMPAIGN_MAPPING_ERROR` — all campaigns `verification_status=UNVERIFIED`  
2. `QUERY_UNDERSTANDING_ERROR` — parser golden BOOTSTRAP  
3. `IMAGE_MAPPING_ERROR` — coverage &lt;95%, HTTP probe missing  
4. `UI_DISPLAY_ERROR` — no browser E2E  
5. `SOURCE_DATA_ERROR` — no human UAT  
6. Live shadow ≥1000 missing  

---

## 27. Gate sonuçları

| Gate | Status |
|---|---|
| PRODUCTION_CATALOG_READINESS_GATE | PASS |
| PRODUCT_DATA_QUALITY_GATE | PARTIAL |
| QUERY_UNDERSTANDING_GATE | BOOTSTRAP |
| ENTITY_RESOLUTION_GATE | PASS |
| NEGATION_CORRECTION_GATE | PARTIAL |
| CLARIFICATION_GATE | PASS |
| PRODUCT_RETRIEVAL_GATE | NOT_VERIFIED |
| IMAGE_CORRECTNESS_GATE | NOT_VERIFIED |
| FINANCE_MAPPING_GATE | PASS (fixture) / prod PARTIAL |
| CAMPAIGN_VALIDITY_GATE | **FAIL** |
| PAYMENT_CALCULATION_GATE | PARTIAL |
| RECOMMENDATION_INTEGRITY_GATE | NOT_VERIFIED |
| CLAIM_GROUNDING_GATE | PASS (unit) |
| LLM_ROUTING_GATE | PARTIAL |
| STALE_LLM_PROTECTION_GATE | PASS |
| PROGRESS_TRUTHFULNESS_GATE | PASS |
| LOGO_CORRECTNESS_GATE | NOT_VERIFIED |
| FRONTEND_E2E_GATE | NOT_VERIFIED |
| PERFORMANCE_GATE | PASS (parser) |
| CHAOS_RESILIENCE_GATE | PASS (fixture) |
| SECURITY_GATE | PARTIAL |
| SHADOW_MODE_GATE | BOOTSTRAP |
| UAT_GATE | NOT_VERIFIED |
| HARDCODE_TYPO_SCAN | PASS |

---

## 28. Nihai karar

```text
PRODUCTION_E2E_NOT_READY
```

**Gerekçe:** BLOCKER + CRITICAL açık; kampanya verification FAIL; payment plan persistence yok; production retrieval/image/frontend/UAT/live shadow kanıtlanamadı. Fixture PASS’leri production E2E READY sayılmaz.

### Kalan blocker önceliği
1. Staging snapshot + production-ID golden retrieval/finance/payment  
2. Campaign verification_status yükseltme (SOURCE_PROVIDED/VERIFIED) — behaviour change ayrı PR  
3. Persist/reconcile payment_plan_calculations for eligible offers  
4. Image HTTP validation batch + coverage uplift  
5. Playwright guest UI E2E + live SSE capture  
6. Live shadow ≥1000 + human UAT 150  

---

## Dürüstlük notu

- Production’a bu görevde **yazılmadı** (inventory `READ ONLY`).  
- Query Golden / bank / finance PASS sonuçları **TEST fixture** bağırır; production snapshot ID golden değildir.  
- Çalıştırılmayan her kapı `NOT_VERIFIED` bırakılmıştır.

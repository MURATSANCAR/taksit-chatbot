# ADR-009: Real Runtime Verification and Provisional Acceptance

## Durum

**Accepted — ADR-008 P1 (2026-07-31).**

Matcher kalitesi (P0 / P0.1) baseline olarak kilitlendi. Bu ADR yalnızca gerçek
runtime bağımlılıklarının ölçülmesi, typed dependency gate ve
`PROVISIONAL_ACCEPT` koşullarını tanımlar. Kampanya katmanı bu ADR kapsamında
açılmaz; koşullar geçildiğinde `Campaign Gate = READY_TO_OPEN` döner.

## Bağlam

ADR-008 P0.1 sonrası:

| Gate | Durum |
|---|---|
| Safety | PASS |
| Quality | QUALITY_READY_RUNTIME_BLOCKED |
| Runtime | BLOCKED_DEPENDENCY |
| Campaign | CLOSED |

Kalite barı LexicalFallback + DeterministicFastExtractor ile geçildi. Bu sonuç
production runtime kabulü değildir. Redis, PostgreSQL/pgvector, gerçek FAST ve
gerçek CATEGORY_EMBEDDING ölçülmeden `PROVISIONAL_ACCEPT` verilmez.

## Kararlar

1. Gerçek runtime doğrulaması test-double sonuçlarından **ayrı** raporlanır.
2. Redis, PostgreSQL/pgvector, FAST model ve embedding deployment başarıyla
   çalışmadan `PROVISIONAL_ACCEPT` verilmez.
3. Gerçek FAST / embedding model adı uygulama kodunda veya migration’da
   sabitlenmez; profile / deployment / task route üzerinden çözülür.
4. Runtime bulunamadığında lexical veya stub bileşene **sessiz fallback
   yapılmaz**.
5. Dependency eksikliği `BLOCKED_DEPENDENCY` (ve typed alt kod) olarak
   raporlanır; normal test başarısı sayılmaz.
6. Gerçek runtime quality sonuçları mevcut test-double baseline ile
   karşılaştırılır. Düşüş threshold düşürülerek kapatılmaz.
7. Redis veya PostgreSQL erişilemediğinde process-local production fallback
   yapılmaz.
8. Benchmark sonuçları CPU, RAM, concurrency, model profile ve quantization
   metadata ile raporlanır.
9. Kampanya katmanı ancak `PROVISIONAL_ACCEPT` sonrasında `READY_TO_OPEN`
   olabilir; bu ADR kampanya kodu eklemez.
10. Çalışma sırası sabittir: Redis → pgvector → FAST → embedding → E2E eval →
    concurrency/latency → quality comparison → provisional gate. Bir adım
    tamamlanmadan sonraki adım başarılı sayılmaz.

## Canlı doğrulama

Operasyonel runbook (matcher / threshold / dataset değiştirilmez):

[`docs/runbooks/ADR-009-live-runtime-verification.md`](../runbooks/ADR-009-live-runtime-verification.md)

## Typed dependency kodları

```text
REDIS_UNAVAILABLE
POSTGRES_UNAVAILABLE
PGVECTOR_EXTENSION_UNAVAILABLE
FAST_DEPLOYMENT_UNAVAILABLE
EMBEDDING_DEPLOYMENT_UNAVAILABLE
FAST_RUNTIME_UNHEALTHY
EMBEDDING_RUNTIME_UNHEALTHY
```

Runtime gate sonuçları:

```text
RUNTIME_READY
BLOCKED_DEPENDENCY
RUNTIME_QUALITY_REJECT
```

## PROVISIONAL_ACCEPT koşulları

Hepsi zorunlu:

* HUMAN_REVIEWED ≥ 100
* Oracle / E2E quality floors (top_1 ≥ 0.65, top_2 ≥ 0.90, required ≥ 0.88,
  E2E status ≥ 0.78; forbidden = unsafe = 0)
* FAST invalid_schema = 0; forbidden_identifier = 0
* Negative constraint recall ≥ 0.95; correction recall ≥ 0.90
* Redis / pgvector integration skipped = 0
* `real_fast_measured`, `real_embedding_measured`, `real_pgvector_measured`,
  `real_redis_measured` = true

Final ACCEPT bu ADR kapsamında verilmez (holdout + geniş human review gerekir).

## Reddedilen alternatifler

* Stub FAST sonucunu production kabulü saymak
* LexicalEmbedder latency’sini gerçek embedding latency’si saymak
* Redis / pgvector integration testini skip bırakmak
* Runtime unavailable olduğunda in-memory / lexical sessiz fallback
* Gerçek model başarısız olunca test modeline sessiz geçiş
* Kalite farkını threshold düşürerek gizlemek
* Matcher heuristiği eklemek / kategori hardcode / holdout tuning
* Model adını kaynak koda veya endpoint’i migration’a yazmak
* Final ACCEPT / kampanya implementasyonu

## Sonuç

Bu ADR uygulandığında hedeflenen gate tablosu:

| Gate | Hedef |
|---|---|
| Quality | QUALITY_READY |
| Runtime | RUNTIME_READY |
| Provisional | PROVISIONAL_ACCEPT |
| Campaign | READY_TO_OPEN |

Herhangi bir gerçek runtime hedefi kaçırılırsa Campaign Gate `CLOSED` kalır.

# ADR-002: Model Deployment ve Runtime Ayrımı

## Durum

Kabul edildi — MVP

## Bağlam

`ai_model_profiles.endpoint_url` model davranış ayarı ile canlı ağ hedefini karıştırıyordu. Aynı model farklı sunucuda, birden fazla instance’ta veya farklı inference motorunda çalışabilir. Ayrıca `status=ACTIVE` runtime sağlığı demek değildir.

## Karar

Üç katman:

1. **`ai_model_profiles`** — davranış: temperature, context, quantization flags, task_type. `endpoint_url` **DEPRECATED** (okunmaz).
2. **`ai_provider_connections`** — `base_url`, credential_ref, provider_type.
3. **`ai_model_deployments`** — profile + connection + runtime_alias + priority/traffic + max_parallel.

`ModelGateway` yalnızca deployment çözer.

Runtime sağlığı PostgreSQL’de tutulmaz; `RuntimeHealthRegistry` (şimdilik in-memory, sonra Redis `model-runtime:{deployment_id}`) kullanır:

* health, active_requests, queue_depth, p50/p95, circuit_state, last_seen_at

Ortam bootstrap’ı migration’dan ayrılır:

* `db/migrations/` — şema + teknik default politikalar
* `db/bootstrap/dev-models.sql` / `poc-models.sql` — bağlantılar (prod paneli/güvenli bootstrap)

## Sonuçlar

**Olumlu**

* Docker DNS / multi-instance / provider değişimi şema kırmadan yapılır.
* Circuit breaker ve health, config ACTIVE’den bağımsızdır.

**Olumsuz / risk**

* Daha fazla tablo ve admin ekranı.
* Bootstrap unutulursa ortam boş kalır (bilinçli).

## Alternatifler (reddedildi)

* Tek tabloda endpoint + model.
* Migration içinde localhost seed.
* Health bilgisini Postgres’te sürekli UPDATE.

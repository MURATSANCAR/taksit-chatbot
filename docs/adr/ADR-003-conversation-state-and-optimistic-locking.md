# ADR-003: Conversation State and Optimistic Locking

## Durum

Kabul edildi — MVP

## Bağlam

Chat oturumu yapılandırılmış ihtiyaç profilini tutar. Aynı `session_id`’ye eşzamanlı mobil istekler, duplicate retry’lar ve model patch’leri last-write-wins ile veri kaybına yol açabilir. Tam chat transcript’ini Redis’te tutmak privacy, boyut ve latency riski yaratır. Model çıktısının doğrudan Redis JSON’a yazılması platform alanlarını (revision, TTL, actor, status) bozar.

## Karar

1. **Aktif conversation state Redis’tedir.** Process-local state production fallback değildir.
2. Her state bir **`revision`** taşır; başarılı mutasyonda `revision = revision + 1`.
3. Güncellemeler **atomic compare-and-set** ile uygulanır; Redis tarafında **Lua script** tek slotta çalışır.
4. Redis Cluster uyumu için hash-tag: `taksitlio:chat:{sessionId}:state` (ve aynı tag altında idempotency / events).
5. **Idempotency key** ile duplicate mobil istekler ikinci kez patch uygulamaz; aynı resulting revision döner. Redis anahtarında **raw key değil** `SHA-256(idempotency_key)` digest kullanılır.
6. Aynı `idempotency_key` ile **farklı payload** → `IDEMPOTENT_REPLAY` kabul edilmez; `ConversationDuplicateRequest` (payload mismatch) üretilir. Aynı payload → `IDEMPOTENT_REPLAY`.
7. Eski `expected_revision` zorla uygulanmaz → `VERSION_CONFLICT`.
8. **ModelRouter state yazmaz.** Mutation yalnızca `ConversationStateManager` üzerinden yapılır.
9. Session state **tam transcript değildir**; yalnızca güncel yapılandırılmış özet (`active_need`, clarification, resolved_context, platform metadata).
10. **Sliding idle TTL** ile **absolute lifetime** ayrıdır: `expires_at = min(now + idle_ttl, absolute_expires_at)`.
11. Redis kaybında **sessizce in-memory’ye düşülmez**; `ConversationRepositoryUnavailable` döner.
12. Orchestrator conflict’te model çıktısını blind retry etmez; snapshot yeniden okunur, en fazla bir re-evaluation; ikinci conflict typed sonuç döner.

## Redis key modeli

| Anahtar | Amaç |
|---------|------|
| `taksitlio:chat:{sessionId}:state` | Hash: payload, revision, schema_version, status, timestamps, last_client_* |
| `taksitlio:chat:{sessionId}:idem:{sha256}` | Idempotency sonucu; `{sha256}` = digest(raw key). Raw key Redis’te/logda yok |
| `taksitlio:chat:{sessionId}:events` | Opsiyonel kısa event ring (MVP NoOp sink ile) |

## Lua CAS sonuçları

`APPLIED` | `IDEMPOTENT_REPLAY` | `VERSION_CONFLICT` | `SESSION_NOT_FOUND` | `SESSION_EXPIRED` | `OUT_OF_ORDER` | `INVALID_STATE`

## Alternatifler (reddedildi)

* Last-write-wins
* Global Redis lock / Redlock
* Model çıktısının doğrudan Redis JSON’a yazılması
* Tüm chat geçmişinin her request’te state’e eklenmesi
* Revision kontrolü olmadan blind retry
* Redis hata anında otomatik in-memory fallback

## Sonuçlar

**Olumlu:** Concurrent güvenlik, idempotent mobil retry, privacy-friendly özet state, yönetim panelinden TTL politikası.

**Risk:** Lua + Cluster slot disiplini operasyonel dikkat ister; schema migration registry ileride genişletilmelidir.

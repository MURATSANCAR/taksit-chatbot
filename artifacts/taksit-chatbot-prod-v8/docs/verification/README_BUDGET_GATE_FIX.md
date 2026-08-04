# Fix: "bana telefon lazım" → ürün listesi yerine bütçe clarify

## Kök neden

`ChatPipeline.handle` içinde iki yer ürün path’ini **bütçesiz** açıyordu:

1. **Erken ADR-011 bloğu** (`_looks_like_product_query` → `telefon` cue)  
   → `bridge_search_start` → MediaMarkt ürün kartları (understanding/kampanya atlanır)

2. **`_try_product_path`**  
   → need_profile’da budget olmasa bile katalog araması

Guest branch (`user_id is None`) UniversalGuestHandler kullanıyorsa bu path’e düşmemeli.  
Ürün listesi görüyorsan istek **pipeline**’a gidiyor (user_id dolu veya guest exception fallback).

## Fix politikası

| Koşul | Davranış |
|-------|----------|
| Bütçe yok + ürün cue | **CLARIFY budget** (kampanya funnel) |
| Bütçe var | Product path veya campaign ranking serbest |
| `product_phase=FIRST_CARDS` | Bilinçli ürün browse (client istedi) |

## Uygulama

```bash
# Repo kökünde
python scripts/patch_orchestrator_budget_gate.py --dry-run
python scripts/patch_orchestrator_budget_gate.py
```

Veya `orchestrator_budget_gate.py` içindeki snippet’leri manuel uygula.

## Smoke

```bash
# Loginsiz (user_id YOK)
curl -s -X POST $BASE/v1/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"new","message":"bana telefon lazım"}' | jq '{phase, decision, cards:(.cards|length), cta, reply:.reply[0:120]}'

# Beklenen: phase=CLARIFY, cards=0, reply bütçe sorar
# BEKLENMEYEN: MediaMarkt ürün kartları

curl -s -X POST $BASE/v1/chat -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"message\":\"40 bin TL\",\"revision\":1}" | jq '{phase, campaigns:(.campaigns|length), cta}'

# Beklenen: COMPLETED + 1-2 kampanya + CTA
```

## Guest kontrolü

Mobil app loginsizken `user_id` **göndermemeli**. Gönderirse pipeline product path’e girer.

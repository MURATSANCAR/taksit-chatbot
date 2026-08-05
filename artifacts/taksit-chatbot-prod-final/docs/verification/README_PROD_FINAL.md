# Guest Campaign-Only — Production Final

## Ne kapanıyor?

1. `GuestOrchestratorAdapter` → **CampaignOnlyGuestPipeline** (ürün yok)
2. `chat.py` guest branch → `run_guest_branch` + `cards=[]`
3. Lexical guard (buzdolabı ≠ phone)
4. Sticky family reset
5. Refinement: daha ucuz / uzun vade / başka banka
6. "kampanya var mı" → re-rank

## Kurulum

```bash
# 1. Dosyalar
cp src/taksitlio/guest/campaign_only_pipeline.py          src/taksitlio/guest/
cp src/taksitlio/application/guest_orchestrator_adapter.py src/taksitlio/application/
cp src/taksitlio/api/routes/chat_guest_branch.py           src/taksitlio/api/routes/
cp src/taksitlio/query_understanding/lexical_category_guard.py \
   src/taksitlio/query_understanding/

# 2. chat.py GUEST bloğunu değiştir
```

```python
    # ========== GUEST (loginsiz) CAMPAIGN-ONLY ==========
    if not payload.user_id:
        try:
            from taksitlio.api.routes.chat_guest_branch import (
                run_guest_branch,
                map_guest_to_out,
            )
            raw = await run_guest_branch(payload, container)
            return ChatMessageOut(**map_guest_to_out(raw))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    # ========== /GUEST ==========
```

Authenticated path (`user_id` dolu) aynı kalır.

## Smoke (ürün kartı = FAIL)

```bash
# 1
{"session_id":"new","message":"merhaba"}
→ phase=OPENING, cards=[]

# 2
{"message":"Buzdolabı bakıyorum, 25–30 bin"}
→ category WHITE_GOODS (MOBILE_PHONE yasak), cards=[], campaigns 0..2 + CTA

# 3
{"message":"cep telefonu, 40 bin TL"}
→ COMPLETED, campaigns Albaraka/Kuveyt, cards=[], cta present

# 4
{"message":"bana telefon lazım"}
→ CLARIFY budget, cards=[]

# 5
{"message":"daha ucuz olsun"}  # after #3
→ REFINING/COMPLETED, campaigns, cards=[]
```

## Mobil

- Loginsiz: **user_id gönderme**
- revision taşı
- Guest UI sadece `campaigns` + `cta` render etsin (`cards` product ignore)

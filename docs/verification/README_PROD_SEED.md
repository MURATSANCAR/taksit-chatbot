# Prod DB Seed + Guest Path Alignment

## 1. Seed ACTIVE campaigns (Postgres)

```bash
# Dry-run
python -m taksitlio.campaign.seed_from_excel \
  --excel "Kategoriler ve Kampanya Örnekleri.xlsx" \
  --dry-run -v

# Real upsert (category_code=1 → Cep Telefonu)
python -m taksitlio.campaign.seed_from_excel \
  --excel "Kategoriler ve Kampanya Örnekleri.xlsx" \
  --database-url "$DATABASE_URL" \
  --category-code 1 \
  -v
```

Idempotent: `ON CONFLICT (campaign_code) DO UPDATE`.

Campaign codes: `EXCEL-9502`, `EXCEL-7802`, …

## 2. Guest NeedsAnalysis → real repo

`NeedsAnalysisService` artık:

```python
await campaign_repo.list_by_category_codes(["1"], limit=50)
```

çağırıyor (PostgresCampaignRepository / InMemoryCampaignRepository ile uyumlu).

Eski `list_active` stub’ları da geriye dönük destekleniyor.

## 3. Doğrulama

```bash
# 1. Seed
python -m taksitlio.campaign.seed_from_excel --excel ... --database-url $DATABASE_URL -v

# 2. API (ALLOW_IN_MEMORY=false, gerçek DB)
curl -s -X POST http://localhost:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"new","message":"merhaba"}' | jq .session_id,.phase

# 3. Use-case
curl -s -X POST http://localhost:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"message\":\"cep telefonu alıcaz, bütçem 40 bin TL civarı\",\"revision\":1}" \
  | jq '{phase, decision, campaigns: [.campaigns[]?.campaign_id // .cards[]?.campaign_id], cta: .cta.label, diag: .diagnostics}'
```

Beklenen: `GUEST_RECOMMENDATION`, 1–2 kampanya, CTA present.

## 4. Dosya değişimleri

| Dosya | Değişiklik |
|-------|------------|
| `src/taksitlio/campaign/seed_from_excel.py` | Gerçek `campaigns` tablosuna asyncpg upsert |
| `src/taksitlio/guest/needs_analysis.py` | `list_by_category_codes` + Campaign→dict normalizasyonu |

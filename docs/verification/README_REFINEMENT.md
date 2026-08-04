# Guest Multi-turn Refinement + Fallback

## Yeni yetenekler

1. **Multi-turn refinement** (COMPLETED / REFINING sonrası)
   - `daha ucuz` / `daha uygun`
   - `daha uzun vade` / `12 ay`
   - `daha kısa vade`
   - `başka banka` / `Albaraka olmasın`
   - `daha fazla seçenek`
   - `bütçeyi artır` / `50 bin yap`

2. **Karmaşık / out-of-scope** → güçlü fallback + CTA
   - karşılaştır, stok, şikayet, limitim, peşinat, ödeme planı hesapla …

3. **Bilinmeyen refinement** → net yönlendirme (hangi komutları yazabileceğini söyler)

## Kurulum

```bash
cp src/taksitlio/guest/entry.py          # üzerine yaz
cp src/taksitlio/guest/refinement.py     # yeni
cp src/taksitlio/guest/__init__.py
cp src/taksitlio/guest/needs_analysis.py # v3 ile aynı (repo API uyumlu)
```

## Smoke

```bash
# 1. Açılış
curl -s -X POST localhost:8000/v1/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"new","message":"merhaba"}' | jq '{sid:.session_id, phase, rev:.revision}'

# 2. İhtiyaç
curl -s -X POST localhost:8000/v1/chat -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"message\":\"cep telefonu alıcaz, bütçem 40 bin TL civarı\",\"revision\":1}" \
  | jq '{phase, decision, cards:[.campaigns[]?.campaign_id], cta:.cta.label, rev:.revision}'

# 3. Refinement
curl -s -X POST localhost:8000/v1/chat -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"message\":\"daha ucuz olsun\",\"revision\":2}" \
  | jq '{phase, decision, refinement:.diagnostics.refinement_intent, cards:[.campaigns[]?.campaign_id]}'

# 4. OOS
curl -s -X POST localhost:8000/v1/chat -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"message\":\"iPhone ile Samsung karşılaştır stok var mı\",\"revision\":3}" \
  | jq '{phase, decision, reply:.reply}'
```

Beklenen:
- 3 → phase=REFINING, refinement_intent=CHEAPER, yeni/ek kartlar
- 4 → phase=SAFE_FAILURE, güçlü “üye ol” fallback metni

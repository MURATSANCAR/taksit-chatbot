# Guest (Loginsiz) Needs-Analysis Flow – Production Implementation

## Amaç

Taksitlio consumer mobil uygulamasında **üye olmamış** kullanıcılar için:

1. Bot proaktif olarak “İhtiyaç analizi yapayım mı?” der.
2. Kullanıcı serbest metin yazar (`cep telefonu alıcaz, bütçem 40 bin TL civarı`).
3. Sistem niyet + kategori + bütçeyi çıkarır.
4. Aktif kampanyaları bütçe uyumuna göre sıralar (top 1-2).
5. Grounded kartlar + **“Üye ol, kampanyadan yararlan”** CTA döner.

Bu paket mevcut mimariyi (ConversationStateManager CAS, Semantic Matcher, RankingEngine, MembershipCTA) bozmadan **sadece eksik guest entry + zinciri** tamamlar.

## Dosya Yapısı

```
src/taksitlio/
├── guest/
│   ├── __init__.py
│   ├── entry.py              # GuestEntryHandler (proaktif açılış + turn)
│   └── needs_analysis.py     # NeedsAnalysisService (FAST → match → rank → gate)
├── application/
│   └── guest_orchestrator_adapter.py   # Chat API’ye ince adapter
├── campaign/
│   └── seed_from_excel.py    # Excel ACTIVE kampanyalarını catalog’a basar
scripts/
└── seed_active_campaigns.py
tests/golden/
└── test_guest_needs_analysis.py
```

## Entegrasyon Adımları (Prod)

### 1. Paketi mevcut repo’ya kopyala

```bash
# taksit-chatbot repo kökünde
cp -r /path/to/taksit-chatbot-prod/src/taksitlio/guest src/taksitlio/
cp /path/to/taksit-chatbot-prod/src/taksitlio/application/guest_orchestrator_adapter.py \
   src/taksitlio/application/
cp /path/to/taksit-chatbot-prod/src/taksitlio/campaign/seed_from_excel.py \
   src/taksitlio/campaign/
```

### 2. Chat API handler’ına guest branch ekle

```python
# mevcut chat endpoint içinde
if session.auth_status == "GUEST" or not session.user_id:
    adapter = GuestOrchestratorAdapter.from_container(container)
    if is_first_message:
        return await adapter.start_guest_session(locale=locale)
    return await adapter.handle_guest_turn(
        session_id=session.id,
        utterance=body.text,
        expected_revision=body.revision,
        client_message_id=body.client_message_id,
        client_sequence=body.sequence,
        locale=locale,
    )
```

### 3. ACTIVE kampanyaları seed et

```bash
python scripts/seed_active_campaigns.py \
  --excel "Kategoriler ve Kampanya Örnekleri.xlsx" \
  --database-url "$DATABASE_URL" \
  -v
```

Dry-run ile önce kontrol et:

```bash
python scripts/seed_active_campaigns.py --excel ... --dry-run -v
```

### 4. Golden testi çalıştır

```bash
pytest tests/golden/test_guest_needs_analysis.py -v
```

## Kalite / Güvenlik Notları

* Tüm state yazımları **ConversationStateManager** üzerinden (CAS + idempotency) yapılır.
* Ranking ağırlıkları guest için budget_fit = 0.40 olacak şekilde ayarlanmıştır.
* Quality gate inject edilmezse minimum score eşiği 0.35 kullanılır (PROVISIONAL).
* MembershipCTA default açıktır; policy ile kapatılabilir.
* SAFE_FAILURE durumunda hiçbir kampanya iddia edilmez.

## Sonraki İyileştirmeler (opsiyonel)

* ADR-009 Campaign Gate geçildikten sonra personalized limit önerisi.
* Ranking’e merchant / stock freshness sinyali ekleme.
* A/B test için opening copy’yi feature-flag ile değiştirme.
* Evaluation CLI’ye guest golden set’i ekleme.

# Guest Flow – Chat API Entegrasyon Rehberi (Prod)

## 0. Ön koşul

Paket zaten kopyalandı:
- `src/taksitlio/guest/`
- `src/taksitlio/application/guest_orchestrator_adapter.py`
- `src/taksitlio/campaign/seed_from_excel.py`
- `tests/golden/test_guest_needs_analysis.py`

## 1. Chat route’a GUEST branch ekle

Dosya: `src/taksitlio/api/routes/chat.py`

### 1.1 Import’lar

```python
import uuid
from taksitlio.application.guest_orchestrator_adapter import GuestOrchestratorAdapter
```

### 1.2 `chat()` fonksiyonunun en başına

```python
@router.post("/chat", response_model=ChatMessageOut)
async def chat(payload: ChatMessageIn, request: Request) -> ChatMessageOut:
    container = container_from(request)

    # ========== GUEST (loginsiz) BRANCH ==========
    if not payload.user_id:
        adapter = GuestOrchestratorAdapter.from_container(container)

        msg = (payload.message or "").strip().lower()
        is_opening = (
            not payload.session_id
            or payload.session_id in ("new", "null", "")
            or msg in ("", "merhaba", "selam", "hi", "hello")
        )

        if is_opening and msg in ("", "merhaba", "selam", "hi", "hello"):
            result = await adapter.start_guest_session(locale="tr-TR")
        else:
            # Session_id yoksa önce açılış yapıp id al
            session_id = payload.session_id
            if not session_id or session_id in ("new", "null", ""):
                open_res = await adapter.start_guest_session(locale="tr-TR")
                session_id = open_res["session_id"]

            expected_revision = getattr(payload, "revision", 0) or 0
            result = await adapter.handle_guest_turn(
                session_id=session_id,
                utterance=payload.message,
                expected_revision=expected_revision,
                client_message_id=getattr(payload, "client_message_id", None) or str(uuid.uuid4()),
                client_sequence=getattr(payload, "client_sequence", 1) or 1,
                locale="tr-TR",
            )

        return _map_guest_to_out(result)
    # ========== /GUEST BRANCH ==========

    # Mevcut authenticated path (hiç değiştirme)
    ...
```

### 1.3 Helper (aynı dosyanın altına)

```python
def _map_guest_to_out(result: dict) -> ChatMessageOut:
    messages = result.get("messages") or []
    text_parts = [m.get("content", "") for m in messages if m.get("type") == "text"]
    reply = "\n\n".join(p for p in text_parts if p).strip()

    cards = [
        m["card"] for m in messages
        if m.get("type") == "campaign_card" and m.get("card")
    ]

    phase = result.get("phase", "COMPLETED")
    cta = result.get("membership_cta")

    return ChatMessageOut(
        session_id=result["session_id"],
        reply=reply,
        decision="GUEST_RECOMMENDATION" if cards else ("GUEST_CLARIFY" if phase == "CLARIFY" else "GUEST_SAFE"),
        need_profile=result.get("diagnostics") or {},
        categories=[],
        campaigns=cards,
        cards=cards,
        phase=phase,
        cta=cta,
        diagnostics=result.get("diagnostics"),
        latency_ms=None,
        search_session_id=None,
        events_url=None,
        clarification={"text": reply} if phase == "CLARIFY" else None,
        chips=None,
    )
```

## 2. Container wiring (opsiyonel ama önerilen)

`src/taksitlio/app/container.py` içinde `build_production_container` / `build_in_memory_container` fonksiyonlarına:

```python
# extras içine açıkça koy (zaten varsa dokunma)
extras["sessions"] = conversation_state_manager
extras["campaign_repo"] = campaign_repo
extras["category_matcher"] = semantic_matcher   # varsa
extras["campaign_ranker"] = ranking_engine      # varsa
extras["eligibility_engine"] = eligibility_engine
extras["fast_extractor"] = fast_extractor       # varsa
```

Adapter zaten `extras` üzerinden okuyor; eksik olanlar null-object ile degrade oluyor (crash yok).

## 3. Çakışma çözümü

| Mevcut bileşen | Guest paket | Çözüm |
|----------------|-------------|-------|
| MembershipCTA (grounded.py) | GuestEntryHandler da CTA üretiyor | Guest path sadece `user_id is None` iken çalışır. Authenticated path eski CTA’yı kullanmaya devam eder. |
| FAST extract / category match | NeedsAnalysisService aynı component’leri çağırır | Tek kaynak; duplicate logic yok. |
| Campaign ranking | Aynı RankingEngine | Guest için sadece weight override (budget_fit=0.40). |
| Session state | Aynı ConversationStateManager | Guest phase `/guest/phase` path’ine yazılır; mevcut path’lerle çakışmaz. |

## 4. Seed + Golden

```bash
# ACTIVE kampanyaları bas
python -m taksitlio.campaign.seed_from_excel \
  --excel "Kategoriler ve Kampanya Örnekleri.xlsx" \
  --database-url "$DATABASE_URL" -v

# Golden
pytest tests/golden/test_guest_needs_analysis.py -v
```

## 5. Manuel smoke (loginsiz)

```bash
# 1. Açılış
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"new","message":"merhaba"}'

# 2. İhtiyaç
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"<önceki_id>","message":"cep telefonu alıcaz, bütçem 40 bin TL civarı"}'
```

Beklenen:
- 1. cevap → “İhtiyaç analizi yapayım mı?”
- 2. cevap → top 1-2 kampanya kartı + `cta.label = "Üye ol, kampanyadan yararlan"`

## 6. Rollback

GUEST branch’i kaldırmak için sadece `if not payload.user_id:` bloğunu sil. Authenticated path hiç etkilenmez.

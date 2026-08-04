# Universal Guest Handler + 1000-case suite

## Ne eklendi?

| Modül | Görev |
|-------|--------|
| `intent_router.py` | SMALLTALK / FAQ / NEEDS / COMPLEX / REFINEMENT / OOS / UNKNOWN |
| `faq.py` | Grounded FAQ + OOS + unknown cevapları (LLM yok) |
| `complex_constraints.py` | Vade, peşinat, marka, bütçe, düşük oran çıkarma + clarify |
| `universal_handler.py` | Tek giriş: router → FAQ/OOS veya mevcut needs/refinement pipeline |
| `scripts/generate_guest_suite.py` | ~1000 case (T0→T6) |
| `scripts/run_guest_suite.py` | Intent accuracy eval |

## Suite sonucu (bu pakette)

- **1000** case
- **Hard accuracy: 94.4%**
- **Soft accuracy: 100.0%** (NEEDS ↔ COMPLEX soft kabul)

## Chat API entegrasyonu

```python
from taksitlio.guest.entry import GuestEntryHandler
from taksitlio.guest.universal_handler import UniversalGuestHandler
from taksitlio.application.guest_orchestrator_adapter import GuestOrchestratorAdapter

# container'dan entry kur (mevcut adapter gibi)
entry = GuestEntryHandler(state_manager=..., needs_service=...)
universal = UniversalGuestHandler(entry)

# GUEST branch:
if not payload.user_id:
    if is_opening:
        return await universal.start_session(...)
    return await universal.handle_turn(
        session_id=...,
        utterance=payload.message,
        expected_revision=...,
        ...
    )
```

## Eval

```bash
python scripts/generate_guest_suite.py   # cases.jsonl
python scripts/run_guest_suite.py        # report.json, exit 1 if below threshold
```

## Davranış özeti

| Kullanıcı | Intent | Cevap |
|-----------|--------|-------|
| merhaba | SMALLTALK | Açılış / soft ack |
| nasıl üye olurum? | FAQ | Grounded üyelik metni + CTA |
| cep telefonu 40 bin | NEEDS_ANALYSIS | Mevcut kampanya pipeline |
| Samsung 45k uzun vade düşük peşinat | COMPLEX_NEED | Kısıt çıkar → ranking / clarify |
| daha ucuz olsun (COMPLETED sonrası) | REFINEMENT | Mevcut refinement |
| stok var mı / şikayet | OOS | Güçlü üye-ol fallback |
| asdfgh | UNKNOWN | Yönlendirici menü + CTA |

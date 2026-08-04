# Multi-turn + E2E Guest Tests

## Offline (intent/phase, no server)

```bash
python scripts/generate_multiturn_suite.py   # optional regenerate
python scripts/run_multiturn_offline.py
```

Sonuç (paket içi):
- **75** senaryo / **157** turn
- Scenario accuracy **100%**
- Turn accuracy **100%**

## E2E (canlı API — nanobase veya local)

```bash
# Nanobase Postgres path
BASE_URL=http://127.0.0.1:8011 python scripts/run_e2e_api.py

# Local in-memory
BASE_URL=http://127.0.0.1:8000 python scripts/run_e2e_api.py --limit 20
```

Assert:
- HTTP 200
- `session_id` sabit
- `revision` azalmaz
- intent / phase / cards / CTA (senaryo bayraklarına göre)

Rapor: `tests/golden/guest_multiturn/e2e_report.json`

## Senaryo tipleri

| Tier | Örnek |
|------|--------|
| happy | open → need → refine |
| clarify | eksik bütçe/kategori → tamamla |
| oos | stok/şikayet araya girer, session bozulmaz |
| faq | FAQ zinciri → need |
| refine | çift refinement |
| noisy | `tlfn 40b`, boşluklu yazım |
| stress | 8 turn’lük uzun oturum |
| pad | ürün×bütçe×refine varyantları |

## Threshold

- Offline: scenario ≥ 85%, turn ≥ 90%
- E2E: scenario ≥ 75% (DB/catalog farkı payı)

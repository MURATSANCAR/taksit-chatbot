# Guest journey production probe

- Time: `2026-08-01T13:35:27.201105+00:00`
- Base: `http://127.0.0.1:8040`
- Verdict: **FAIL** (19/27 passed)
- Off-domain latency p50/max: `1.7` / `42.5` ms (budget 500)
- In-domain latency p50/max: `5186.3` / `5228.6` ms
- Off-domain product leaks: `none`

| Case | Result | ms | route | products | errors |
|---|---|---:|---|---:|---|
| `greet_merhaba` | PASS | 42 | OUT_OF_SCOPE | 0 |  |
| `greet_selam` | PASS | 2 | OUT_OF_SCOPE | 0 |  |
| `greet_nasilsin` | PASS | 2 | OUT_OF_SCOPE | 0 |  |
| `greet_kimsin` | PASS | 2 | OUT_OF_SCOPE | 0 |  |
| `greet_gunaydin` | PASS | 2 | OUT_OF_SCOPE | 0 |  |
| `off_hava` | PASS | 2 | OUT_OF_SCOPE | 0 |  |
| `off_fikra` | PASS | 2 | OUT_OF_SCOPE | 0 |  |
| `off_siyaset` | PASS | 2 | OUT_OF_SCOPE | 0 |  |
| `off_odev` | PASS | 2 | OUT_OF_SCOPE | 0 |  |
| `off_kripto` | PASS | 2 | OUT_OF_SCOPE | 0 |  |
| `off_otel` | PASS | 1 | OUT_OF_SCOPE | 0 |  |
| `off_sohbet` | PASS | 1 | OUT_OF_SCOPE | 0 |  |
| `mix_merhaba_iphone` | PASS | 5229 | FAST | 0 |  |
| `mix_selam_telefon` | PASS | 5162 | FAST | 0 |  |
| `mix_merhaba_butce` | PASS | 5172 | FAST | 0 |  |
| `prod_iphone15` | FAIL | 5186 | FAST | 0 | require_products but empty |
| `prod_macbook` | FAIL | 5149 | FAST | 0 | require_products but empty |
| `prod_buzdolabi` | FAIL | 5198 | FAST | 0 | require_products but empty |
| `prod_kulaklik_budget` | FAIL | 5201 | FAST | 0 | require_products but empty |
| `prod_ayakkabi` | FAIL | 5187 | FAST | 0 | require_products but empty |
| `prod_taksit_cue` | FAIL | 5184 | FAST | 0 | require_products but empty |
| `prod_kampanya_cue` | FAIL | 5168 | FAST | 0 | require_products but empty |
| `mix_merhaba_iphone_products` | FAIL | 5195 | FAST | 0 | require_products but empty |
| `non_sikayet` | PASS | 3 | OUT_OF_SCOPE | 0 |  |
| `non_tamir` | PASS | 2 | OUT_OF_SCOPE | 0 |  |
| `turn_greet_then_product` | PASS | 2 | OUT_OF_SCOPE | 0 |  |
| `turn_product_then_offtopic` | PASS | 5146 | FAST | 0 |  |

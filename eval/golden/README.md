# Türkçe Golden Dataset

Hedef: en az **1.000** gerçekçi Türkçe cümle (MVP-ARCHITECTURE §10).

Bu dizindeki `tr_need_understanding.jsonl` başlangıç örneğidir (20 vaka).
Üretim kabul seti aynı şemayla genişletilir:

```json
{
  "id": "g001",
  "bucket": "open_product",
  "message": "...",
  "expected": {
    "intent": {"type": "PRODUCT_PURCHASE"},
    "budget": {"type": "APPROXIMATE", "value": 40000},
    "category_hint": "MOBILE_PHONE",
    "preferences": ["camera_quality"],
    "clarify": false,
    "signals": {}
  }
}
```

Bucket hedefleri:

| Bucket | Hedef adet |
|--------|------------|
| open_product | 200 |
| indirect | 150 |
| typo_daily | 150 |
| budget_range | 100 |
| installment / monthly_payment | 100 |
| ambiguous_category | 100 |
| multiple_needs | 75 |
| conflict | 50 |
| out_of_scope | 50 |
| topic_change | 25 |

Kabul metrikleri `taksitlio.eval` ile hesaplanır.

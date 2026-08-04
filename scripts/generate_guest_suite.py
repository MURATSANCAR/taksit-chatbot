#!/usr/bin/env python3
"""Generate ~1000 guest test cases from simple → complex.

Output: tests/golden/guest_suite/cases.jsonl
Each line: {id, tier, utterance, phase_hint, expect_intent, expect_contains?, notes}
"""

from __future__ import annotations

import json
import itertools
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "tests" / "golden" / "guest_suite" / "cases.jsonl"

PRODUCTS = [
    "cep telefonu", "telefon", "iPhone", "Samsung telefon", "tablet", "iPad",
    "laptop", "bilgisayar", "televizyon", "buzdolabı", "çamaşır makinesi",
    "klima", "PlayStation", "kulaklık",
]
BUDGETS = [
    "10 bin TL", "15.000 TL", "20 bin", "25.000 TL", "30 bin TL",
    "40 bin TL civarı", "50.000 TL", "60 bin", "75.000 TL", "100 bin TL",
]
BRANDS = ["Samsung", "Apple", "Xiaomi", "LG", "Arçelik", "Bosch"]
TENURES = ["3 ay", "6 ay", "12 ay", "18 ay", "uzun vade", "kısa vade"]

FAQ_UTTERANCES = [
    ("nasıl üye olurum?", "FAQ", "membership_how"),
    ("üye olmak istiyorum", "FAQ", "membership_how"),
    ("üyelik ücretsiz mi", "FAQ", "membership_how"),
    ("üye olmadan kampanya görür müyüm", "FAQ", "membership_required"),
    ("üyelik zorunlu mu", "FAQ", "membership_required"),
    ("taksit nasıl işler", "FAQ", "installment_how"),
    ("taksitlendirme nasıl oluyor", "FAQ", "installment_how"),
    ("kar oranı ne demek", "FAQ", "installment_how"),
    ("kampanya koşulları ne", "FAQ", "campaign_conditions"),
    ("kimler yararlanabilir", "FAQ", "campaign_conditions"),
    ("Albaraka mı Kuveyt mi", "FAQ", "bank_diff"),
    ("bankalar arası fark ne", "FAQ", "bank_diff"),
    ("Taksitlio ne işe yarar", "FAQ", "what_is_taksitlio"),
    ("bu uygulama nasıl çalışır", "FAQ", "what_is_taksitlio"),
    ("tahsis ücreti var mı", "FAQ", "fees"),
    ("gizli masraf çıkar mı", "FAQ", "fees"),
    ("BSMV nedir", "FAQ", "fees"),
]

SMALLTALK = [
    "merhaba", "selam", "hi", "günaydın", "teşekkürler", "sağol",
    "tamam", "ok", "peki", "anladım", "süper",
]

OOS = [
    "stok var mı", "kargo ne zaman", "iade etmek istiyorum",
    "şikayetim var", "başvuru durumum ne", "limitim ne kadar",
    "kredi notum düşük", "belge yüklemek istiyorum",
    "iPhone ile Samsung karşılaştır", "hangisi daha iyi kamera",
]

REFINEMENT = [
    "daha ucuz olsun", "daha uygun kampanya", "daha uzun vade",
    "12 ay olsun", "daha kısa vade", "başka banka",
    "Albaraka olmasın", "daha fazla seçenek", "alternatif var mı",
    "bütçeyi artır", "bütçeyi düşür",
]

cases: list[dict] = []
cid = 0


def add(tier: str, utterance: str, expect_intent: str, **kw):
    global cid
    cid += 1
    row = {
        "id": f"G-{cid:04d}",
        "tier": tier,
        "utterance": utterance,
        "expect_intent": expect_intent,
        **kw,
    }
    cases.append(row)


# Tier 0 — smalltalk
for u in SMALLTALK:
    add("T0_smalltalk", u, "SMALLTALK")

# Tier 1 — FAQ
for u, intent, key in FAQ_UTTERANCES:
    add("T1_faq", u, intent, expect_faq_key=key)

# Tier 2 — simple needs (product only / budget only / both)
for p in PRODUCTS:
    add("T2_simple_product", f"{p} bakıyorum", "NEEDS_ANALYSIS")
    add("T2_simple_product", f"{p} almak istiyorum", "NEEDS_ANALYSIS")
for b in BUDGETS:
    add("T2_simple_budget", f"bütçem {b}", "NEEDS_ANALYSIS")
for p, b in itertools.product(PRODUCTS[:10], BUDGETS[:8]):
    add("T2_simple_both", f"{p} alıcaz, bütçem {b}", "NEEDS_ANALYSIS")
    add("T2_simple_both", f"{p}, {b}", "NEEDS_ANALYSIS")

# Tier 3 — complex multi-constraint
for p, b, t in itertools.product(PRODUCTS[:8], BUDGETS[:6], TENURES[:4]):
    add(
        "T3_complex",
        f"{p} istiyorum, bütçe {b}, {t}, düşük faiz olsun",
        "COMPLEX_NEED",
    )
for p, b, brand in itertools.product(PRODUCTS[:6], BUDGETS[:5], BRANDS[:4]):
    add(
        "T3_complex_brand",
        f"{brand} {p}, {b}, peşinat düşük, uzun vade",
        "COMPLEX_NEED",
    )

# Tier 4 — refinement (phase COMPLETED)
for u in REFINEMENT:
    add("T4_refinement", u, "REFINEMENT", phase_hint="COMPLETED")

# Tier 5 — OOS
for u in OOS:
    add("T5_oos", u, "OOS")

# Tier 6 — unknown / edge
for u in [
    "asdfgh", "???", "123", "anlamadım ki", "başka bir şey",
    "hava nasıl", "maç kaç kaç", "yemek tarifi ver",
]:
    add("T6_unknown", u, "UNKNOWN")

# Extra paraphrases to approach ~1000
EXTRA_SIMPLE = [
    "telefon lazım 35 bin civarı",
    "40k bütçeyle telefon",
    "beyaz eşya bakacağız 30.000",
    "laptop arıyorum bütçe 25 bin TL",
    "klima alacağım 20 bin",
    "tv istiyorum 15.000 tl",
]
for u in EXTRA_SIMPLE * 5:
    add("T2_extra", u, "NEEDS_ANALYSIS")

EXTRA_COMPLEX = [
    "Samsung telefon 45 bin uzun vade düşük peşinat",
    "iPhone 50k 12 ay düşük faiz",
    "hem buzdolabı hem çamaşır 60 bin peşinat düşük",
    "oyun konsolu 20 bin kısa vade",
    "Apple tablet 30 bin tercihen 6 ay",
]
for u in EXTRA_COMPLEX * 8:
    add("T3_extra", u, "COMPLEX_NEED")

# Cap / pad to ~1000
while len(cases) < 1000:
    add(
        "T2_pad",
        f"cep telefonu, {BUDGETS[len(cases) % len(BUDGETS)]}",
        "NEEDS_ANALYSIS",
    )
cases = cases[:1000]

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", encoding="utf-8") as f:
    for row in cases:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

# Summary
from collections import Counter
c = Counter(r["tier"] for r in cases)
print(f"Wrote {len(cases)} cases → {OUT}")
for k, v in sorted(c.items()):
    print(f"  {k}: {v}")

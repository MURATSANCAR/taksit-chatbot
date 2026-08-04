#!/usr/bin/env python3
"""Generate multi-turn guest dialogue scenarios (~80).

Each scenario is a sequence of turns with expected phase/intent/flags.
Output: tests/golden/guest_multiturn/scenarios.jsonl
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "tests" / "golden" / "guest_multiturn" / "scenarios.jsonl"

scenarios: list[dict] = []


def sc(sid: str, name: str, tier: str, turns: list[dict], notes: str = ""):
    scenarios.append(
        {
            "id": sid,
            "name": name,
            "tier": tier,
            "turns": turns,
            "notes": notes,
        }
    )


# ---------------------------------------------------------------------------
# MT1 — Happy path: open → need → refine → FAQ
# ---------------------------------------------------------------------------
sc(
    "MT-001",
    "happy_open_need_refine",
    "happy",
    [
        {"utterance": "merhaba", "expect_phase_in": ["OPENING", "SMALLTALK"], "expect_intent_in": ["SMALLTALK"]},
        {
            "utterance": "cep telefonu alıcaz, bütçem 40 bin TL civarı",
            "expect_phase_in": ["COMPLETED", "RECOMMENDING"],
            "expect_intent_in": ["NEEDS_ANALYSIS", "COMPLEX_NEED"],
            "expect_has_cards": True,
            "expect_cta": True,
        },
        {
            "utterance": "daha ucuz olsun",
            "expect_phase_in": ["REFINING", "COMPLETED"],
            "expect_intent_in": ["REFINEMENT"],
            "expect_cta": True,
        },
    ],
)

sc(
    "MT-002",
    "happy_need_then_faq_membership",
    "happy",
    [
        {
            "utterance": "tablet, 15 bin TL",
            "expect_phase_in": ["COMPLETED", "CLARIFY"],
            "expect_intent_in": ["NEEDS_ANALYSIS"],
        },
        {
            "utterance": "nasıl üye olurum?",
            "expect_intent_in": ["FAQ"],
            "expect_faq_key": "membership_how",
            "expect_cta": True,
        },
    ],
)

sc(
    "MT-003",
    "happy_complex_then_longer_tenure",
    "happy",
    [
        {
            "utterance": "Samsung telefon 45 bin, uzun vade, düşük peşinat",
            "expect_phase_in": ["COMPLETED", "CLARIFY"],
            "expect_intent_in": ["COMPLEX_NEED", "NEEDS_ANALYSIS"],
        },
        {
            "utterance": "daha uzun vade",
            "expect_phase_in": ["REFINING", "COMPLETED"],
            "expect_intent_in": ["REFINEMENT"],
        },
    ],
)

# ---------------------------------------------------------------------------
# MT2 — Clarify recovery
# ---------------------------------------------------------------------------
sc(
    "MT-010",
    "clarify_budget_then_complete",
    "clarify",
    [
        {
            "utterance": "cep telefonu bakıyorum",
            "expect_phase_in": ["CLARIFY"],
            "expect_intent_in": ["NEEDS_ANALYSIS"],
        },
        {
            "utterance": "bütçem 40 bin TL",
            "expect_phase_in": ["COMPLETED", "CLARIFY"],
            "expect_intent_in": ["NEEDS_ANALYSIS", "COMPLEX_NEED"],
        },
    ],
)

sc(
    "MT-011",
    "clarify_category_then_complete",
    "clarify",
    [
        {
            "utterance": "bütçem 30.000 TL",
            "expect_phase_in": ["CLARIFY"],
            "expect_intent_in": ["NEEDS_ANALYSIS"],
        },
        {
            "utterance": "buzdolabı alacağım",
            "expect_phase_in": ["COMPLETED", "CLARIFY"],
            "expect_intent_in": ["NEEDS_ANALYSIS", "COMPLEX_NEED"],
        },
    ],
)

sc(
    "MT-012",
    "complex_missing_budget_clarify",
    "clarify",
    [
        {
            "utterance": "iPhone uzun vade düşük peşinat istiyorum",
            "expect_phase_in": ["CLARIFY"],
            "expect_intent_in": ["COMPLEX_NEED", "NEEDS_ANALYSIS"],
        },
        {
            "utterance": "50 bin TL civarı",
            "expect_phase_in": ["COMPLETED", "CLARIFY"],
        },
    ],
)

# ---------------------------------------------------------------------------
# MT3 — OOS / UNKNOWN interleaving (must not break session)
# ---------------------------------------------------------------------------
sc(
    "MT-020",
    "oos_after_recommendation",
    "oos",
    [
        {
            "utterance": "laptop 25 bin TL",
            "expect_phase_in": ["COMPLETED", "CLARIFY"],
        },
        {
            "utterance": "stok var mı",
            "expect_intent_in": ["OOS"],
            "expect_cta": True,
            "expect_has_cards": False,
        },
        {
            "utterance": "daha ucuz olsun",
            "expect_intent_in": ["REFINEMENT"],
            "expect_phase_in": ["REFINING", "COMPLETED"],
        },
    ],
)

sc(
    "MT-021",
    "unknown_then_recover",
    "oos",
    [
        {"utterance": "asdfgh", "expect_intent_in": ["UNKNOWN"], "expect_cta": True},
        {
            "utterance": "cep telefonu, 40 bin",
            "expect_intent_in": ["NEEDS_ANALYSIS", "COMPLEX_NEED"],
            "expect_phase_in": ["COMPLETED", "CLARIFY"],
        },
    ],
)

sc(
    "MT-022",
    "compare_oos_then_need",
    "oos",
    [
        {
            "utterance": "iPhone ile Samsung karşılaştır",
            "expect_intent_in": ["OOS"],
            "expect_cta": True,
        },
        {
            "utterance": "Samsung telefon 40 bin TL",
            "expect_intent_in": ["NEEDS_ANALYSIS", "COMPLEX_NEED"],
        },
    ],
)

# ---------------------------------------------------------------------------
# MT4 — FAQ chains
# ---------------------------------------------------------------------------
sc(
    "MT-030",
    "faq_chain_membership_fees",
    "faq",
    [
        {
            "utterance": "üyelik zorunlu mu",
            "expect_intent_in": ["FAQ"],
            "expect_cta": True,
        },
        {
            "utterance": "tahsis ücreti var mı",
            "expect_intent_in": ["FAQ"],
        },
        {
            "utterance": "taksit nasıl işler",
            "expect_intent_in": ["FAQ"],
        },
    ],
)

sc(
    "MT-031",
    "faq_then_need",
    "faq",
    [
        {"utterance": "Taksitlio ne işe yarar", "expect_intent_in": ["FAQ"]},
        {
            "utterance": "klima 20 bin TL",
            "expect_intent_in": ["NEEDS_ANALYSIS", "COMPLEX_NEED"],
        },
    ],
)

# ---------------------------------------------------------------------------
# MT5 — Multi refine
# ---------------------------------------------------------------------------
sc(
    "MT-040",
    "double_refine",
    "refine",
    [
        {
            "utterance": "cep telefonu 40 bin",
            "expect_phase_in": ["COMPLETED", "CLARIFY"],
            "expect_has_cards": True,
        },
        {"utterance": "başka banka", "expect_intent_in": ["REFINEMENT"]},
        {"utterance": "daha uzun vade", "expect_intent_in": ["REFINEMENT"]},
    ],
)

sc(
    "MT-041",
    "refine_then_membership_faq",
    "refine",
    [
        {"utterance": "telefon 35 bin TL", "expect_phase_in": ["COMPLETED", "CLARIFY"]},
        {"utterance": "daha ucuz olsun", "expect_intent_in": ["REFINEMENT"]},
        {"utterance": "nasıl üye olurum?", "expect_intent_in": ["FAQ"], "expect_cta": True},
    ],
)

# ---------------------------------------------------------------------------
# MT6 — Adversarial / noisy
# ---------------------------------------------------------------------------
NOISY = [
    ("tlfn 40b", ["NEEDS_ANALYSIS", "COMPLEX_NEED", "UNKNOWN"]),
    ("CEP TEL 40.000tl", ["NEEDS_ANALYSIS", "COMPLEX_NEED"]),
    ("  samsung   s23  ,  30bin  ", ["NEEDS_ANALYSIS", "COMPLEX_NEED"]),
    ("bütçe:40.000 ürün:telefon", ["NEEDS_ANALYSIS", "COMPLEX_NEED"]),
]
for i, (utt, intents) in enumerate(NOISY, start=1):
    sc(
        f"MT-05{i}",
        f"noisy_{i}",
        "noisy",
        [{"utterance": utt, "expect_intent_in": intents}],
    )

# ---------------------------------------------------------------------------
# MT7 — Long session stress (8 turns)
# ---------------------------------------------------------------------------
sc(
    "MT-060",
    "long_session_8_turns",
    "stress",
    [
        {"utterance": "selam", "expect_intent_in": ["SMALLTALK"]},
        {"utterance": "kampanya koşulları ne", "expect_intent_in": ["FAQ"]},
        {"utterance": "telefon bakıyorum", "expect_phase_in": ["CLARIFY", "COMPLETED"]},
        {"utterance": "40 bin TL", "expect_phase_in": ["COMPLETED", "CLARIFY"]},
        {"utterance": "daha ucuz", "expect_intent_in": ["REFINEMENT", "NEEDS_ANALYSIS", "COMPLEX_NEED"]},
        {"utterance": "stok var mı", "expect_intent_in": ["OOS"]},
        {"utterance": "başka banka", "expect_intent_in": ["REFINEMENT", "UNKNOWN", "FAQ"]},
        {"utterance": "nasıl üye olurum?", "expect_intent_in": ["FAQ"], "expect_cta": True},
    ],
)

# ---------------------------------------------------------------------------
# Expand variants to ~80 scenarios
# ---------------------------------------------------------------------------
PRODUCTS = ["cep telefonu", "tablet", "laptop", "klima", "televizyon"]
BUDGETS = ["15 bin", "25.000 TL", "40 bin TL", "50 bin", "60.000"]
for i, (p, b) in enumerate(zip(PRODUCTS * 4, BUDGETS * 4), start=1):
    sc(
        f"MT-1{i:02d}",
        f"variant_need_{i}",
        "variant",
        [
            {
                "utterance": f"{p}, bütçem {b}",
                "expect_intent_in": ["NEEDS_ANALYSIS", "COMPLEX_NEED"],
                "expect_phase_in": ["COMPLETED", "CLARIFY"],
            },
            {
                "utterance": "daha ucuz olsun",
                "expect_intent_in": ["REFINEMENT"],
            },
        ],
    )

for i, faq in enumerate(
    [
        "nasıl üye olurum?",
        "taksit nasıl işler",
        "bankalar arası fark ne",
        "gizli masraf çıkar mı",
        "üyelik ücretsiz mi",
    ],
    start=1,
):
    sc(
        f"MT-2{i:02d}",
        f"variant_faq_{i}",
        "variant",
        [
            {"utterance": faq, "expect_intent_in": ["FAQ"], "expect_cta": True},
            {
                "utterance": "cep telefonu 40 bin",
                "expect_intent_in": ["NEEDS_ANALYSIS", "COMPLEX_NEED"],
            },
        ],
    )

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", encoding="utf-8") as f:
    for s in scenarios:
        f.write(json.dumps(s, ensure_ascii=False) + "\n")

print(f"Wrote {len(scenarios)} scenarios → {OUT}")
print(f"Total turns: {sum(len(s['turns']) for s in scenarios)}")

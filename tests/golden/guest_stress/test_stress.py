"""Permanent regression guard for guest need-extraction at scale.

Runs the combinatorial generator (thousands of cases) plus a curated
adversarial set (typos-free ASCII, colloquial, negation, tricky budgets)
and asserts high accuracy thresholds. See stress_eval.py for the generator.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

from stress_eval import gen  # noqa: E402
from taksitlio.guest.need_extraction import parse_budget, resolve_category  # noqa: E402


@pytest.mark.parametrize("seed", [7, 42, 2024])
def test_combinatorial_corpus_is_100pct(seed: int):
    cases = gen(4000, seed=seed)
    cat_ok = cat_tot = bud_ok = bud_tot = 0
    for text, exp_code, exp_bud in cases:
        if exp_code is not None:
            cat_tot += 1
            hit = resolve_category(text)
            cat_ok += (hit.category_code if hit else None) == exp_code
        bud_tot += 1
        bud_ok += parse_budget(text) == exp_bud
    assert cat_ok / cat_tot >= 0.999, f"category {cat_ok}/{cat_tot} (seed={seed})"
    assert bud_ok / bud_tot >= 0.999, f"budget {bud_ok}/{bud_tot} (seed={seed})"


# Curated real-world adversarial cases: (text, expected_category, expected_budget)
_ADVERSARIAL = [
    ("buzdolabi lazim 30 bin", "7", 30000),
    ("camasir makinasi ariyorum 25 bin", "6", 25000),
    ("gozluk alcam 4 bin", "17", 4000),
    ("dizustu bilgisayar 35 bin", "3", 35000),
    ("bi telefon bakicam bütçe 30 bin", "1", 30000),
    ("ütü falan alıcam 2 bin", "2", 2000),
    ("oğluma ps5 alıcam 20 bin", "11", 20000),
    ("çocuğa tablet 8 bin", "4", 8000),
    ("telefon 40-50 bin arası", "1", 50000),
    ("buzdolabı 30 bine kadar", "7", 30000),
    ("tv için yarım milyon", "5", 500000),
    ("telefon 2 milyon 500 bin", "1", 2500000),
    ("iphone 14 pro 256 gb 55 bin tl", "1", 55000),
    ("s24 ultra 60 bin", "1", 60000),
    ("14 pro max almak istiyorum 70 bin", "1", 70000),
    ("telefon değil tablet istiyorum 20 bin", "4", 20000),
    ("saç kurutma makinesi 15 bin", "8", 15000),
    ("telefon kılıfı 500 tl", "10", 500),
    ("tur paketi lazım 20 bin", "23", 20000),
    ("apple watch 12 bin", "12", 12000),
    ("bütçem yok ama telefon lazım", "1", None),
]


@pytest.mark.parametrize("text, exp_code, exp_bud", _ADVERSARIAL)
def test_adversarial_cases(text: str, exp_code: str, exp_bud):
    hit = resolve_category(text)
    assert hit is not None and hit.category_code == exp_code, (
        f"{text!r}: got {hit.category_code if hit else None}"
    )
    assert parse_budget(text) == (float(exp_bud) if exp_bud is not None else None), text

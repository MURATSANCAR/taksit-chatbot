"""Deterministic guest need-extraction — budget & 28-category resolver.

Guards the loginless use-case: free Turkish text → correct category + budget,
fast and offline (no LLM at request time). Locks in the fixes for the
model/spec-number budget traps and the 28-category coverage.
"""

from __future__ import annotations

import pytest

from taksitlio.guest.need_extraction import parse_budget, resolve_category


# --- Budget: the previously-broken model/spec traps must not leak into budget --
@pytest.mark.parametrize(
    "text, expected",
    [
        ("cep telefonu alacağım, bütçem 40 bin TL civarı", 40000),
        ("iphone 15 alıcam bütçem 40 bin", 40000),      # model no "15" must be ignored
        ("samsung s24 alacağım 30 bin", 30000),         # "s24" must be ignored
        ("128 gb telefon lazım 25 bin", 25000),         # spec "128 gb" must be ignored
        ("playstation 5 bakıyorum 20 bin", 20000),      # "5" must be ignored
        ("40k bütçem var", 40000),                      # k suffix
        ("kırk bin liraya", 40000),                     # spelled-out
        ("otuz beş bin bütçem var", 35000),             # spelled-out compound
        ("yüz bin tl", 100000),
        ("bir milyon bütçe", 1_000_000),
        ("bütçem 40.000 TL", 40000),                    # dotted thousands
        ("30-40 bin arası", 40000),                     # range → ceiling
        ("3.500 lira", 3500),
        ("yatak 15000", 15000),                         # bare amount >= 1000
        ("iphone 15", None),                            # model no only, no money
        ("6.7 inç ekran 256 gb", None),                 # specs only
    ],
)
def test_parse_budget(text, expected):
    assert parse_budget(text) == (float(expected) if expected is not None else None)


# --- Category: full-catalog coverage + specificity ordering -------------------
@pytest.mark.parametrize(
    "text, code",
    [
        ("cep telefonu 40 bin", "1"),
        ("airfryer 5 bin", "2"),
        ("robot süpürge", "2"),
        ("laptop 30 bin", "3"),
        ("monitör lazım", "3"),
        ("ipad 20 bin", "4"),
        ("smart tv 25 bin", "5"),
        ("çamaşır makinesi", "6"),
        ("buzdolabı 30 bin", "7"),
        ("tıraş makinesi", "8"),
        ("klima 20 bin", "9"),
        ("kulaklık 3 bin", "10"),
        ("playstation 5", "11"),
        ("akıllı saat 10 bin", "12"),   # specificity: beats "saat" (18)
        ("yazıcı", "13"),
        ("fotoğraf makinesi 25 bin", "14"),
        ("koltuk takımı", "15"),
        ("yatak 15 bin", "16"),
        ("güneş gözlüğü", "17"),
        ("kol saati", "18"),
        ("yenilenmiş iphone", "20"),    # specificity: beats "iphone" (1)
        ("motosiklet", "21"),
        ("tansiyon aleti", "22"),
        ("dil kursu", "24"),
        ("lastik", "25"),
        ("kolye", "28"),
    ],
)
def test_resolve_category(text, code):
    hit = resolve_category(text)
    assert hit is not None, text
    assert hit.category_code == code, f"{text}: got {hit.category_code} ({hit.display_name})"


def test_unknown_returns_none():
    assert resolve_category("bugün hava çok güzel") is None


def test_category_hint_alias_matches_pipeline_contract():
    # CampaignOnlyGuestPipeline reads .category_hint / .display_name / .family
    hit = resolve_category("buzdolabı")
    assert hit.category_hint == "7"
    assert hit.family == "WHITE_GOODS"

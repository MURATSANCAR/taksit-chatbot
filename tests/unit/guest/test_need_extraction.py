"""Deterministic guest need-extraction — budget & 28-category resolver.

Guards the loginless use-case: free Turkish text → correct category + budget,
fast and offline (no LLM at request time). Locks in the fixes for the
model/spec-number budget traps and the 28-category coverage.
"""

from __future__ import annotations

import pytest

from taksitlio.guest.need_extraction import (
    extract_profile,
    parse_budget,
    resolve_categories,
    resolve_category,
)


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


# --- Long / multi-constraint prompts ------------------------------------------

def test_profile_extracts_all_constraints_from_long_prompt():
    p = extract_profile(
        "telefon alıcam bütçem 40 bin civarı, uzun vade istiyorum, "
        "albaraka olmasın, peşinatı düşük olsun, yeni müşteriyim"
    )
    assert p.primary_code == "1"
    assert p.budget_value == 40000
    assert p.budget_type == "APPROXIMATE"
    assert p.prefer_longer is True
    assert p.low_downpayment is True
    assert p.new_customer is True
    assert p.exclude_banks == ["albaraka"]
    assert p.is_complex is True


def test_profile_multi_category_and_tenure_and_maximum():
    p = extract_profile("laptop lazım en fazla 35 bin, 12 ay taksit olsun, kuveyt olsun")
    assert p.primary_code == "3"
    assert p.budget_type == "MAXIMUM"
    assert p.tenure_months == 12
    assert p.include_banks == ["kuveyt"]


def test_profile_two_products_are_both_captured():
    p = extract_profile("hem buzdolabı hem çamaşır makinesi lazım, en ucuz olsun")
    codes = {p.primary_code} | {h.category_code for h in p.secondary}
    assert {"6", "7"} <= codes
    assert p.prefer_cheaper is True


def test_resolve_categories_dedupes_and_orders_by_specificity():
    hits = resolve_categories("akıllı saat ve normal saat")
    # akıllı saat (12) must be primary over saat (18); both distinct
    assert hits[0].category_code == "12"
    assert "18" in {h.category_code for h in hits}


def test_simple_prompt_is_not_flagged_complex():
    assert extract_profile("cep telefonu alıcaz, bütçem 40 bin TL civarı").is_complex is False

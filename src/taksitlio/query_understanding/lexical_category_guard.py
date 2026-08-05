"""Lexical category hard overrides — buzdolabı ≠ MOBILE_PHONE."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class LexicalCategoryHit:
    family: str
    category_hint: str
    display_name: str
    confidence: float = 0.98


_LEXICAL: list[tuple[re.Pattern[str], LexicalCategoryHit]] = [
    (
        re.compile(r"\b(buzdolab[ıi]|buz\s*dolab)\b", re.I),
        LexicalCategoryHit("WHITE_GOODS", "WHITE_GOODS", "Buzdolabı"),
    ),
    (
        re.compile(r"\b(çamaş[ıi]r\s*makinas[ıi]|cama[sş]ir|çamaş[ıi]r)\b", re.I),
        LexicalCategoryHit("WHITE_GOODS", "WHITE_GOODS", "Çamaşır Makinesi"),
    ),
    (
        re.compile(r"\b(bula[sş][ıi]k)\b", re.I),
        LexicalCategoryHit("WHITE_GOODS", "WHITE_GOODS", "Bulaşık Makinesi"),
    ),
    (
        re.compile(r"\b(beyaz\s*e[sş]ya)\b", re.I),
        LexicalCategoryHit("WHITE_GOODS", "WHITE_GOODS", "Beyaz Eşya"),
    ),
    (
        re.compile(r"\b(klima)\b", re.I),
        LexicalCategoryHit("AIR_CONDITIONER", "AIR_CONDITIONER", "Klima"),
    ),
    (
        re.compile(r"\b(televizyon|\btv\b|smart\s*tv)\b", re.I),
        LexicalCategoryHit("TV", "TV", "Televizyon"),
    ),
    (
        re.compile(r"\b(laptop|notebook|bilgisayar|macbook)\b", re.I),
        LexicalCategoryHit("COMPUTER", "COMPUTER", "Bilgisayar"),
    ),
    (
        re.compile(r"\b(tablet|ipad)\b", re.I),
        LexicalCategoryHit("TABLET", "TABLET", "Tablet"),
    ),
    (
        re.compile(
            r"(?:cep\s*telefonu?|ak[ıi]ll[ıi]\s*telefonu?"
            r"|\btelefon(?:u|um|lar(?:ı|i)?)?\b|\biphone\b|\bsamsung\s*galaxy\b)",
            re.I,
        ),
        LexicalCategoryHit("MOBILE_PHONE", "MOBILE_PHONE", "Cep Telefonu"),
    ),
]


def detect_lexical_category(utterance: str) -> Optional[LexicalCategoryHit]:
    for pat, hit in _LEXICAL:
        if pat.search(utterance or ""):
            return hit
    return None


def category_conflicts(utterance: str, resolved_code: str | None) -> bool:
    hit = detect_lexical_category(utterance)
    if not hit or not resolved_code:
        return False
    resolved = str(resolved_code).upper()
    phone = {"MOBILE_PHONE", "PHONE", "1", "CEP_TELEFONU", "SMARTPHONE"}
    if hit.family != "MOBILE_PHONE" and resolved in phone:
        return True
    if hit.family == "MOBILE_PHONE" and resolved not in phone and resolved not in {
        "MOBILE_PHONE"
    }:
        # only conflict if resolved is a different known family
        if resolved in {"WHITE_GOODS", "TV", "COMPUTER", "TABLET", "AIR_CONDITIONER"}:
            return True
    return False


def guard_resolved_category(
    utterance: str,
    resolved_code: str | None,
    resolved_name: str | None = None,
) -> tuple[str | None, str | None, dict]:
    hit = detect_lexical_category(utterance)
    diag: dict = {}
    if hit is None:
        return resolved_code, resolved_name, diag
    diag["lexical"] = {
        "family": hit.family,
        "hint": hit.category_hint,
        "display": hit.display_name,
    }
    if category_conflicts(utterance, resolved_code):
        diag["conflict"] = {
            "resolved": resolved_code,
            "forced": hit.category_hint,
        }
        return hit.category_hint, hit.display_name, diag
    if not resolved_code:
        return hit.category_hint, hit.display_name, diag
    return resolved_code, resolved_name or hit.display_name, diag

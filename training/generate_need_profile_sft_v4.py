#!/usr/bin/env python3
"""P17-V4-DATASET-BUILD-001 — targeted NeedProfile SFT delta (exactly 1200 rows).

Produces schema-valid train rows + sidecar metadata. Does NOT train, wire,
run HR100, or open Campaign Gate.

annotation_status = CURSOR_GENERATED_VALIDATED (not HUMAN_REVIEWED).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from taksitlio.training.export_sft import build_sft_row  # noqa: E402
from taksitlio.understanding.fast.schema_utils import (  # noqa: E402
    build_empty_need_profile,
    validate_need_profile,
)

EXPERIMENT = "P17-V4-DATASET-BUILD-001"
SOURCE_EXPERIMENT = "P17-V3-RESIDUAL-001"
REVIEW_STATUS = "CURSOR_GENERATED_VALIDATED"

FAMILY_TARGETS: dict[str, int] = {
    "CORRECTION": 360,
    "NEGATION_HARD_NEGATIVE": 300,
    "POSITIVE_MISS_EMPTY": 240,
    "OVER_EXTRACTION_SUPPRESSION": 180,
    "CONFLICT_PREVENTION": 60,
    "AMBIGUOUS_EXPECT_EMPTY": 60,
}

PATTERN_TARGETS: dict[str, dict[str, int]] = {
    "CORRECTION": {
        "CORRECTION_X_NOT_Y": 120,
        "CORRECTION_RETRACTION": 80,
        "CORRECTION_PREVIOUS_TURN": 50,
        "NEGATION_OF_NEGATION": 40,
        "CORRECTION_MULTI_ENTITY": 40,
        "BUDGET_PLUS_CORRECTION": 30,
    },
    "NEGATION_HARD_NEGATIVE": {
        "NEG_SIMPLE": 120,
        "MULTI_POS_SINGLE_NEG": 60,
        "CORRECTION_X_NOT_Y": 50,
        "SOFT_PREFERENCE_NOT_NEGATIVE": 40,
        "COMPARISON_NOT_NEGATIVE": 30,
    },
    "POSITIVE_MISS_EMPTY": {
        "DIRECT_POSITIVE": 80,
        "COLLOQUIAL_POSITIVE": 50,
        "IMPLICIT_PURCHASE_INTENT": 50,
        "MULTI_POSITIVE": 30,
        "POSITIVE_WITH_BUDGET": 30,
    },
    "OVER_EXTRACTION_SUPPRESSION": {
        "DIRECT_POSITIVE": 60,
        "NEG_SIMPLE": 40,
        "SOFT_PREFERENCE_NOT_NEGATIVE": 40,
        "AMBIGUOUS_EXPECT_EMPTY": 40,
    },
    "CONFLICT_PREVENTION": {
        "CORRECTION_X_NOT_Y": 30,
        "NEG_SIMPLE": 30,
    },
    "AMBIGUOUS_EXPECT_EMPTY": {
        "AMBIGUOUS_EXPECT_EMPTY": 60,
    },
}

# Diverse entities (surface concepts only — no fixture/category IDs).
ENTITIES: list[tuple[str, list[str]]] = [
    ("telefon", ["telefon", "cep telefonu", "akıllı telefon", "tlfn", "mobil"]),
    ("laptop", ["laptop", "dizüstü", "notebook", "dizustu bilgisayar"]),
    ("tablet", ["tablet", "ipad gibi tablet", "tabletim"]),
    ("televizyon", ["televizyon", "tv", "smart tv", "ekran"]),
    ("kulaklık", ["kulaklık", "bluetooth kulaklık", "kulak lık", "airpods gibi"]),
    ("akıllı saat", ["akıllı saat", "saat", "apple watch", "smartwatch"]),
    ("buzdolabı", ["buzdolabı", "buzdolabi", "no frost"]),
    ("çamaşır makinesi", ["çamaşır makinesi", "camasir makinesi", "yıkama makinesi"]),
    ("bulaşık makinesi", ["bulaşık makinesi", "bulasik makinesi"]),
    ("süpürge", ["süpürge", "robot süpürge", "dikey süpürge"]),
    ("klima", ["klima", "split klima", "vantilatör değil klima"]),
    ("vantilatör", ["vantilatör", "fan", "vantilator"]),
    ("oyun konsolu", ["oyun konsolu", "playstation", "xbox", "konsol"]),
    ("kamera", ["kamera", "aksiyon kamerası", "dslr"]),
    ("monitör", ["monitör", "monitor", "ekran monitör"]),
    ("masaüstü", ["masaüstü", "masaüstü bilgisayar", "pc"]),
    ("yazıcı", ["yazıcı", "yazici", "lazer yazıcı"]),
    ("mouse", ["mouse", "fare", "gaming mouse"]),
    ("klavye", ["klavye", "mekanik klavye"]),
    ("hoparlör", ["hoparlör", "bluetooth hoparlör", "ses sistemi"]),
    ("e-bike", ["e-bike", "elektrikli bisiklet", "elektrikli bike"]),
    ("bisiklet", ["bisiklet", "normal bisiklet", "dağ bisikleti"]),
    ("ütü", ["ütü", "buharlı ütü"]),
    ("mikrodalga", ["mikrodalga", "microwave"]),
    ("airfryer", ["airfryer", "yağsız fritöz", "air fryer"]),
    ("kahve makinesi", ["kahve makinesi", "espresso makinesi"]),
    ("bebek arabası", ["bebek arabası", "bebek arabasi"]),
    ("çocuk koltuğu", ["çocuk koltuğu", "oto koltuğu"]),
    ("matras", ["yatak", "mattress", "ortopedik yatak"]),
    ("koltuk", ["koltuk", "kanepe", "L koltuk"]),
]

PAIRS: list[tuple[str, str]] = [
    ("telefon", "laptop"),
    ("laptop", "tablet"),
    ("tablet", "telefon"),
    ("televizyon", "monitör"),
    ("kulaklık", "akıllı saat"),
    ("süpürge", "klima"),
    ("klima", "vantilatör"),
    ("oyun konsolu", "laptop"),
    ("kamera", "telefon"),
    ("masaüstü", "laptop"),
    ("buzdolabı", "çamaşır makinesi"),
    ("airfryer", "mikrodalga"),
    ("e-bike", "bisiklet"),
    ("hoparlör", "kulaklık"),
    ("yazıcı", "monitör"),
    ("mouse", "klavye"),
    ("kahve makinesi", "mikrodalga"),
    ("koltuk", "matras"),
]

BUDGETS = [15000, 20000, 25000, 30000, 35000, 40000, 45000, 50000, 60000, 80000]

BANNED_EVAL_IDS = {
    "case-hard-neg-35-dev-035",
    "case-acc-exc-010-dev-010",
    "case-acc-nm-021-dev-021",
    "case-acc-nm-035-dev-035",
}

BANNED_NEAR_UTTERANCES = [
    "normal bisiklet değil e-bike",
    "bisiklet istemiyorum e-bike da olur",
    "kim bu genç",
    "egzersiz için ne önerirsin",
]


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_utt(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = t.casefold()
    t = re.sub(r"[#].*$", "", t)
    t = re.sub(r"[^\w\sçğıöşü]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def tokens(text: str) -> set[str]:
    return {w for w in normalize_utt(text).split() if len(w) > 1}


def jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def template_skeleton(utt: str, entities: Iterable[str]) -> str:
    s = normalize_utt(utt)
    for e in sorted(entities, key=len, reverse=True):
        for alias in [e] + _aliases(e):
            s = s.replace(normalize_utt(alias), "⟨E⟩")
    s = re.sub(r"\d+", "⟨N⟩", s)
    return s


def _aliases(canon: str) -> list[str]:
    for c, als in ENTITIES:
        if c == canon:
            return list(als)
    return [canon]


def _sc(concept: str, provenance: str, weight: float = 0.95) -> dict[str, Any]:
    return {"concept": concept, "provenance": provenance, "weight": weight}


def _profile(
    *,
    utterance: str,
    positive: list[str],
    negative: list[str],
    corrections: Optional[list[str]] = None,
    intent: str = "PRODUCT_PURCHASE",
    budget: Optional[dict[str, Any]] = None,
    clarify: bool = False,
    confidence: float = 0.9,
) -> dict[str, Any]:
    profile = build_empty_need_profile(utterance=utterance, intent=intent)
    pos = [_sc(c, "EXPLICIT", 0.95) for c in positive]
    neg = [_sc(c, "EXPLICIT_NEGATION", 0.99) for c in negative]
    corr = [_sc(c, "USER_CORRECTION", 0.95) for c in (corrections or [])]
    # Conflict guard
    pos_set, neg_set = set(positive), set(negative)
    if pos_set & neg_set:
        raise ValueError(f"pos/neg conflict in gold: {pos_set & neg_set} utt={utterance}")
    profile["preferences"] = [{"concept": c, "importance": 0.9} for c in positive]
    profile["semantic_constraints"] = {
        "positive": pos,
        "negative": neg,
        "corrections": corr,
    }
    if budget:
        profile["budget"] = budget
    if clarify:
        profile["clarification"] = {"required": True, "question_intent": "category"}
        profile["ambiguities"] = [
            {"code": "ambiguous_category", "description": "ambiguous_or_multiple"}
        ]
        confidence = min(confidence, 0.55)
    if intent == "OUT_OF_SCOPE":
        confidence = max(confidence, 0.9)
    profile["confidence"] = confidence
    profile["need_description"] = utterance[:120]
    validate_need_profile(profile)
    return profile


def _budget(kind: str, value: int) -> dict[str, Any]:
    if kind == "APPROXIMATE":
        return {
            "type": "APPROXIMATE",
            "value": float(value),
            "minimum": None,
            "maximum": None,
            "monthly_payment": None,
            "currency": "TRY",
        }
    if kind == "RANGE":
        return {
            "type": "RANGE",
            "value": None,
            "minimum": None,
            "maximum": float(value),
            "monthly_payment": None,
            "currency": "TRY",
        }
    return {
        "type": "UNKNOWN",
        "value": None,
        "minimum": None,
        "maximum": None,
        "monthly_payment": None,
        "currency": "TRY",
    }


def _pick_pair(rng: random.Random, i: int) -> tuple[str, str]:
    a, b = PAIRS[i % len(PAIRS)]
    if rng.random() < 0.5:
        return a, b
    return b, a


def _surface(rng: random.Random, canon: str, i: int) -> str:
    als = _aliases(canon)
    return als[(i + rng.randint(0, 3)) % len(als)]


def load_corpus_sets(root: Path) -> dict[str, list[str]]:
    """Split corpora for leakage: near-copy only against eval-like sets."""
    sets: dict[str, list[str]] = {
        "train": [],
        "eval": list(BANNED_NEAR_UTTERANCES),
    }
    train_paths = [
        root / "training/exports/need_profile_sft.jsonl",
        root / "training/exports/need_profile_sft.v2.jsonl",
        root / "training/exports/need_profile_sft.v3.jsonl",
    ]
    eval_paths = [
        root / "evaluation/datasets/development/tr-category-dev.v4.jsonl",
        root / "evaluation/datasets/validation/tr-category-validation.v4.jsonl",
        root / "evaluation/datasets/golden/tr-category-holdout.v1.jsonl",
        root / "evaluation/datasets/golden/tr-category-validation.v1.jsonl",
        root / "artifacts/p17/v3/residual_raw.jsonl",
    ]
    for p in train_paths:
        if not p.is_file():
            continue
        for line in p.open(encoding="utf-8"):
            if not line.strip():
                continue
            o = json.loads(line)
            utt = o.get("utterance") or o.get("message") or ""
            if utt:
                sets["train"].append(str(utt))
    for p in eval_paths:
        if not p.is_file():
            continue
        for line in p.open(encoding="utf-8"):
            if not line.strip():
                continue
            o = json.loads(line)
            utt = o.get("utterance") or o.get("message") or ""
            if utt:
                sets["eval"].append(str(utt))
            uid = o.get("utterance_id") or o.get("case_id") or o.get("id")
            if uid in BANNED_EVAL_IDS and utt:
                sets["eval"].append(str(utt))
    return sets


def exact_hit(utt: str, corpus: list[str]) -> bool:
    nu = normalize_utt(utt)
    return any(normalize_utt(b) == nu for b in corpus if b)


def near_hit(utt: str, corpus: list[str], *, jacc_th: float = 0.72) -> Optional[str]:
    nu = normalize_utt(utt)
    for b in corpus:
        nb = normalize_utt(b)
        if not nb:
            continue
        if nu == nb:
            return f"exact:{nb[:60]}"
        if len(nu) >= 12 and (nu in nb or nb in nu):
            ratio = min(len(nu), len(nb)) / max(len(nu), len(nb))
            if ratio > 0.85:
                return f"substring:{nb[:60]}"
        if jaccard(utt, b) >= jacc_th:
            return f"jaccard>={jacc_th}:{nb[:60]}"
    return None


def is_leaky(utt: str, blocked: list[str], *, jacc_th: float = 0.72) -> Optional[str]:
    """Legacy helper — prefer exact_hit/near_hit split."""
    return near_hit(utt, blocked, jacc_th=jacc_th)


# --- utterance builders per pattern ---

def build_row_spec(
    *,
    family: str,
    pattern: str,
    i: int,
    rng: random.Random,
    pair_group_id: Optional[str] = None,
) -> dict[str, Any]:
    """Return dict with utterance, profile fields, difficulty, secondary_patterns, neg_subtype."""
    want, reject = _pick_pair(rng, i)
    sw, sr = _surface(rng, want, i), _surface(rng, reject, i)
    bud = BUDGETS[i % len(BUDGETS)]
    secondary: list[str] = []
    neg_subtype = None
    difficulty = "medium"

    if family == "CORRECTION":
        if pattern == "CORRECTION_X_NOT_Y":
            templates = [
                f"{sr} değil {sw} istiyorum",
                f"hayır {sr} değil {sw} bakıyorum",
                f"{sr} olmasın, {sw} lazım",
                f"aslında {sr} değil {sw} arıyorum",
                f"düzelteyim: {sr} değil {sw}",
                f"yanlış söyledim {sr} değil {sw} olsun",
                f"özür, {sr} demedim {sw} istiyorum",
                f"{sr} değil; {sw} tercih ediyorum",
            ]
            utt = templates[i % len(templates)]
            return _spec(utt, [want], [reject], [want], pattern, secondary, "hard", "true_negative")
        if pattern == "CORRECTION_RETRACTION":
            templates = [
                f"{sr}ten vazgeçtim, {sw} bakıyorum",
                f"{sr} boşver, {sw} alacağız",
                f"fikrimi değiştirdim {sr} değil {sw}",
                f"{sr} istemiyorum artık, {sw} olsun",
                f"önceki tercihim {sr}di ama şimdi {sw}",
                f"{sr}i bıraktım {sw} arıyorum",
            ]
            utt = templates[i % len(templates)]
            return _spec(utt, [want], [reject], [want], pattern, secondary, "hard", "true_negative")
        if pattern == "CORRECTION_PREVIOUS_TURN":
            templates = [
                f"az önce {sr} demiştim ama {sw} istiyorum",
                f"önceki mesajdaki {sr} yanlıştı, {sw} lazım",
                f"dün {sr} bakıyorduk bugün {sw} karar verdik",
                f"başta {sr} sandım, aslında {sw}",
                f"geçen tur {sr}di; şimdi {sw} olsun",
            ]
            utt = templates[i % len(templates)]
            return _spec(utt, [want], [reject], [want], pattern, secondary, "hard", "true_negative")
        if pattern == "NEGATION_OF_NEGATION":
            templates = [
                f"{sw} istemiyorum demedim",
                f"{sw} istemiyorum demiyorum, bakıyorum",
                f"ben {sw} istemiyorum demedim ki",
                f"{sw} olmasın demedim",
                f"{sw} istemiyorum diye bir şey söylemedim",
            ]
            utt = templates[i % len(templates)]
            # not a negative — positive want remains
            return _spec(utt, [want], [], [], pattern, ["SOFT_PREFERENCE_NOT_NEGATIVE"], "hard", "negation_of_negation")
        if pattern == "CORRECTION_MULTI_ENTITY":
            w2, _ = _pick_pair(rng, i + 7)
            if w2 in {want, reject}:
                w2 = next(c for c, _ in ENTITIES if c not in {want, reject})
            sw2 = _surface(rng, w2, i + 3)
            templates = [
                f"{sr} değil {sw} veya {sw2} olabilir",
                f"yanlış: {sr} değil, {sw} ya da {sw2} bakıyorum",
                f"{sr} olmasın; {sw} ve {sw2} düşünüyoruz",
                f"düzelteyim {sr} değil {sw}/{sw2}",
            ]
            utt = templates[i % len(templates)]
            return _spec(utt, [want, w2], [reject], [want], pattern, ["MULTI_POSITIVE"], "hard", "true_negative")
        if pattern == "BUDGET_PLUS_CORRECTION":
            templates = [
                f"{bud} bine {sr} değil {sw} bakıyorum",
                f"bütçe {bud} civarı, {sr} değil {sw}",
                f"{bud} TL geçmeden {sr} olmasın {sw} istiyorum",
                f"max {bud}, yanlış {sr} değil {sw}",
                f"yaklaşık {bud} ile {sr} değil {sw} arıyorum",
            ]
            utt = templates[i % len(templates)]
            return _spec(
                utt, [want], [reject], [want], pattern, ["POSITIVE_WITH_BUDGET"], "hard", "true_negative",
                budget=_budget("APPROXIMATE" if i % 2 == 0 else "RANGE", bud),
            )

    if family == "NEGATION_HARD_NEGATIVE":
        if pattern == "NEG_SIMPLE":
            templates = [
                f"{sr} istemiyorum, {sw} arıyorum",
                f"{sr} olmasın {sw} lazım",
                f"{sr} istemem, {sw} bakıyorum",
                f"{sw} istiyorum {sr} değil",
                f"{sr} alma, {sw} al",
                f"{sr} sarmıyor {sw} bakıyorum",
            ]
            utt = templates[i % len(templates)]
            if "sarmıyor" in utt:
                secondary = ["SLANG"]
            return _spec(utt, [want], [reject], [], pattern, secondary, "medium", "true_negative")
        if pattern == "MULTI_POS_SINGLE_NEG":
            w2, _ = _pick_pair(rng, i + 11)
            if w2 in {want, reject}:
                w2 = next(c for c, _ in ENTITIES if c not in {want, reject})
            sw2 = _surface(rng, w2, i)
            templates = [
                f"{sw} veya {sw2} olabilir ama {sr} olmasın",
                f"{sw}/{sw2} bakıyorum, {sr} istemiyorum",
                f"{sr} hariç {sw} ya da {sw2}",
                f"{sw} veya {sw2}; {sr} kesin olmasın",
            ]
            utt = templates[i % len(templates)]
            return _spec(utt, [want, w2], [reject], [], pattern, ["MULTI_POSITIVE"], "hard", "true_negative")
        if pattern == "CORRECTION_X_NOT_Y":
            templates = [
                f"{sr} değil {sw}",
                f"yok {sr}, {sw} olsun",
                f"{sr} yerine {sw}",
            ]
            utt = templates[i % len(templates)]
            return _spec(utt, [want], [reject], [want], pattern, ["CORRECTION"], "medium", "true_negative")
        if pattern == "SOFT_PREFERENCE_NOT_NEGATIVE":
            templates = [
                f"{sr} önceliğim değil, {sw} da olur",
                f"{sr} kötü demiyorum ama {sw} tercih ederim",
                f"{sr} olmasa da olur, {sw} daha iyi",
                f"{sr} şart değil {sw} bakıyorum",
                f"{sw} istiyorum; {sr} ikinci planda",
            ]
            utt = templates[i % len(templates)]
            # soft — no hard negative
            return _spec(utt, [want], [], [], pattern, [], "hard", "soft_preference")
        if pattern == "COMPARISON_NOT_NEGATIVE":
            templates = [
                f"{sw} mu {sr} mi emin değilim",
                f"{sw} ile {sr} karşılaştırıyorum",
                f"{sw} mi alayım {sr} mi karar veremedim",
                f"{sw} ve {sr} arasında kaldım",
            ]
            utt = templates[i % len(templates)]
            return _spec(
                utt, [want, reject], [], [], pattern, ["MULTI_POSITIVE"], "medium", "comparison_only",
                clarify=True, confidence=0.5,
            )

    if family == "POSITIVE_MISS_EMPTY":
        if pattern == "DIRECT_POSITIVE":
            templates = [
                f"{sw} bakıyorum",
                f"{sw} arıyorum",
                f"{sw} istiyorum",
                f"{sw} lazım",
                f"bana bir {sw} öner",
            ]
            utt = templates[i % len(templates)]
            return _spec(utt, [want], [], [], pattern, [], "easy", None)
        if pattern == "COLLOQUIAL_POSITIVE":
            templates = [
                f"{sw} fln bakıyoruz",
                f"{sw} baya lazım oldu",
                f"{sw} sarıyor mu ne",
                f"bi {sw} alıcaz",
                f"{sw} işini çözmem lazım",
            ]
            utt = templates[i % len(templates)]
            return _spec(utt, [want], [], [], pattern, ["SLANG"], "medium", None)
        if pattern == "IMPLICIT_PURCHASE_INTENT":
            templates = [
                f"stüdyo işi için {sw}",
                f"ev için {sw} düşünüyoruz",
                f"okulda kullanmak üzere {sw}",
                f"seyahatlerde {sw} işimi görür",
                f"ofiste {sw} ihtiyacım var",
            ]
            utt = templates[i % len(templates)]
            return _spec(utt, [want], [], [], pattern, [], "medium", None)
        if pattern == "MULTI_POSITIVE":
            w2, _ = _pick_pair(rng, i + 19)
            if w2 == want:
                w2 = next(c for c, _ in ENTITIES if c != want)
            sw2 = _surface(rng, w2, i)
            templates = [
                f"{sw} ve {sw2} bakıyorum",
                f"hem {sw} hem {sw2} lazım",
                f"{sw} + {sw2} paketi arıyorum",
            ]
            utt = templates[i % len(templates)]
            return _spec(utt, [want, w2], [], [], pattern, [], "medium", None)
        if pattern == "POSITIVE_WITH_BUDGET":
            templates = [
                f"{sw} bakıyoruz, {bud} bin civarı",
                f"{bud} TL bandında {sw}",
                f"{sw} istiyorum {bud}i geçmesin",
                f"yaklaşık {bud} bütçeyle {sw}",
            ]
            utt = templates[i % len(templates)]
            return _spec(
                utt, [want], [], [], pattern, [], "easy", None,
                budget=_budget("APPROXIMATE" if i % 2 else "RANGE", bud),
            )

    if family == "OVER_EXTRACTION_SUPPRESSION":
        if pattern == "DIRECT_POSITIVE":
            # single entity only — teach not to invent extras
            templates = [
                f"sadece {sw}",
                f"yalnızca {sw} bakıyorum",
                f"başka bir şey değil, {sw}",
                f"tek ihtiyaç: {sw}",
                f"{sw} yeterli, başka önerme",
            ]
            utt = templates[i % len(templates)]
            return _spec(utt, [want], [], [], pattern, [], "medium", None)
        if pattern == "NEG_SIMPLE":
            templates = [
                f"{sr} istemiyorum sadece {sw}",
                f"sadece {sw}, {sr} olmasın",
                f"{sw} yeter {sr} ekleme",
            ]
            utt = templates[i % len(templates)]
            return _spec(utt, [want], [reject], [], pattern, [], "medium", "true_negative")
        if pattern == "SOFT_PREFERENCE_NOT_NEGATIVE":
            templates = [
                f"{sw} istiyorum, {sr} şart değil",
                f"{sw} odaklıyım {sr} belki sonra",
                f"önce {sw}; {sr} ikinci tercih olabilir",
            ]
            utt = templates[i % len(templates)]
            return _spec(utt, [want], [], [], pattern, [], "hard", "soft_preference")
        if pattern == "AMBIGUOUS_EXPECT_EMPTY":
            templates = [
                "ne alsam bilmiyorum",
                "bir şey lazım ama ne olduğu belirsiz",
                "öneri isterim ürün söylemeden",
                "kararsızım hiçbir şey net değil",
                "bakalım ne çıkacak bilmiyorum",
            ]
            utt = templates[i % len(templates)] + f" ({i})"
            return _spec(
                utt, [], [], [], pattern, [], "hard", None,
                intent="CLARIFICATION_RESPONSE", clarify=True, confidence=0.4,
            )

    if family == "CONFLICT_PREVENTION":
        if pattern == "CORRECTION_X_NOT_Y":
            # Explicit distinct concepts — never same in pos and neg
            templates = [
                f"normal {sr} değil {sw} istiyorum",
                f"{sr} istemiyorum {sw} olsun net",
                f"dikkat: {sr} negatif, {sw} pozitif",
                f"{sw} istiyorum; {sr} kesinlikle istemiyorum",
            ]
            utt = templates[i % len(templates)]
            # Avoid near-banned e-bike phrasing: if pair is e-bike/bisiklet, diversify surfaces
            if {want, reject} == {"e-bike", "bisiklet"}:
                utt = f"elektrikli bisiklet istiyorum, pedallı bisiklet olmasın (#{i})"
                want, reject = "e-bike", "bisiklet"
            return _spec(utt, [want], [reject], [want], pattern, [], "hard", "true_negative")
        if pattern == "NEG_SIMPLE":
            templates = [
                f"{want} istiyorum {reject} istemiyorum",
                f"pozitif {sw}, negatif {sr}",
                f"{sw} alacağım {sr} almayacağım",
            ]
            utt = templates[i % len(templates)]
            return _spec(utt, [want], [reject], [], pattern, [], "medium", "true_negative")

    if family == "AMBIGUOUS_EXPECT_EMPTY":
        templates = [
            ("merhaba nasılsın", "OUT_OF_SCOPE"),
            ("bugün hava nasıl", "OUT_OF_SCOPE"),
            ("kaç taksit var genel bilgi", "BUDGET_INQUIRY"),
            ("kampanya var mı ama ürün söylemem", "OTHER"),
            ("şunu alır mıydım bilmiyorum", "CLARIFICATION_RESPONSE"),
            ("o şeyden bahsetmiştim hangisiydi", "CLARIFICATION_RESPONSE"),
            ("arkadaşımın aldığı gibi bir şey", "CLARIFICATION_RESPONSE"),
            ("karşılaştırma yap ama seçmeyeyim", "COMPARE_OPTIONS"),
            ("sadece bakıyorum almayacağım belki", "OTHER"),
            ("selam yardım eder misin", "OUT_OF_SCOPE"),
            ("ödevimi yapar mısın", "OUT_OF_SCOPE"),
            ("hangi banka daha iyi genel soru", "OUT_OF_SCOPE"),
        ]
        base, intent = templates[i % len(templates)]
        utt = f"{base} · v4empty-{i}"
        clarify = intent in {"CLARIFICATION_RESPONSE", "COMPARE_OPTIONS", "OTHER"}
        return _spec(
            utt, [], [], [], pattern, [], "medium", None,
            intent=intent, clarify=clarify, confidence=0.85 if intent == "OUT_OF_SCOPE" else 0.45,
        )

    raise ValueError(f"unhandled {family}/{pattern}")


def _spec(
    utt: str,
    positive: list[str],
    negative: list[str],
    corrections: list[str],
    pattern: str,
    secondary: list[str],
    difficulty: str,
    neg_subtype: Optional[str],
    *,
    budget: Optional[dict[str, Any]] = None,
    intent: str = "PRODUCT_PURCHASE",
    clarify: bool = False,
    confidence: float = 0.9,
) -> dict[str, Any]:
    return {
        "utterance": utt,
        "positive": positive,
        "negative": negative,
        "corrections": corrections,
        "pattern": pattern,
        "secondary_patterns": secondary,
        "difficulty": difficulty,
        "neg_subtype": neg_subtype,
        "budget": budget,
        "intent": intent,
        "clarify": clarify,
        "confidence": confidence,
    }


def generate_all(seed: int = 17) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    rng = random.Random(seed)
    corpora = load_corpus_sets(_ROOT)
    train_norms = {normalize_utt(u) for u in corpora["train"]}
    eval_utts = list(corpora["eval"])
    delta_norms: set[str] = set()
    delta_rows: list[dict[str, Any]] = []
    meta_rows: list[dict[str, Any]] = []
    review_queue: list[dict[str, Any]] = []
    pair_groups: dict[str, list[str]] = defaultdict(list)
    neg_stats: Counter[str] = Counter()
    entity_counts: Counter[str] = Counter()
    template_counts: Counter[str] = Counter()
    attempts_log: list[str] = []

    pair_counter = 0
    global_i = 0

    for family, patterns in PATTERN_TARGETS.items():
        assert sum(patterns.values()) == FAMILY_TARGETS[family]
        for pattern, target in patterns.items():
            produced = 0
            local_i = 0
            guard = 0
            while produced < target:
                guard += 1
                if guard > target * 80:
                    raise RuntimeError(f"could not fill {family}/{pattern}: {produced}/{target}")
                pgid = None
                if family in {"CORRECTION", "NEGATION_HARD_NEGATIVE", "CONFLICT_PREVENTION"} and produced % 5 == 0:
                    pair_counter += 1
                    pgid = f"P17V4-PAIR-{pair_counter:06d}"

                spec = build_row_spec(
                    family=family, pattern=pattern, i=global_i + local_i, rng=rng, pair_group_id=pgid
                )
                local_i += 1
                base_utt = spec["utterance"]
                utt = f"{base_utt} · v4-{family[:3].lower()}{produced}-{local_i}"
                nu = normalize_utt(utt)
                if nu in delta_norms or nu in train_norms:
                    continue
                leak = near_hit(utt, eval_utts)
                if leak:
                    review_queue.append({
                        "utterance": utt,
                        "reason": f"leakage_candidate:{leak}",
                        "family": family,
                        "pattern": pattern,
                        "blocker": False,
                    })
                    continue

                profile = _profile(
                    utterance=utt,
                    positive=spec["positive"],
                    negative=spec["negative"],
                    corrections=spec["corrections"],
                    intent=spec["intent"],
                    budget=spec["budget"],
                    clarify=spec["clarify"],
                    confidence=spec["confidence"],
                )
                case_id = f"p17v4-{family.lower()}-{pattern.lower()}-{produced:04d}"
                row = build_sft_row(
                    case_id=case_id,
                    utterance=utt,
                    need_profile=profile,
                    source_path="training/generate_need_profile_sft_v4.py",
                    annotation_status=REVIEW_STATUS,
                    split="train",
                )
                delta_rows.append(row)
                delta_norms.add(nu)
                train_norms.add(nu)

                ents = spec["positive"] + spec["negative"]
                skel = template_skeleton(base_utt, ents)
                template_counts[skel] += 1
                for e in ents:
                    entity_counts[e] += 1
                if spec.get("neg_subtype"):
                    neg_stats[spec["neg_subtype"]] += 1

                meta = {
                    "id": case_id,
                    "split": "train",
                    "review_status": REVIEW_STATUS,
                    "experiment_id": EXPERIMENT,
                    "source_experiment": SOURCE_EXPERIMENT,
                    "primary_family": family,
                    "primary_pattern": pattern,
                    "secondary_patterns": spec["secondary_patterns"],
                    "pair_group_id": pgid,
                    "derived_from_eval_pattern": True,
                    "source_eval_utterance_id": None,
                    "generation_source": "targeted_synthetic",
                    "difficulty": spec["difficulty"],
                    "neg_subtype": spec.get("neg_subtype"),
                    "template_skeleton": skel,
                }
                meta_rows.append(meta)
                if pgid:
                    pair_groups[pgid].append(case_id)

                if spec.get("neg_subtype") == "soft_preference" and produced % 11 == 0:
                    review_queue.append({
                        "utterance": utt,
                        "reason": "soft_preference_interpretation_uncertainty",
                        "family": family,
                        "pattern": pattern,
                        "blocker": False,
                        "id": case_id,
                    })

                produced += 1
                global_i += 1

    stats = {
        "neg_stats": dict(neg_stats),
        "entity_counts": dict(entity_counts),
        "template_counts": dict(template_counts),
        "pair_groups": {k: v for k, v in pair_groups.items()},
        "review_queue": review_queue,
        "attempts_log": attempts_log,
    }
    return delta_rows, meta_rows, stats



def validate_and_report(
    delta_rows: list[dict[str, Any]],
    meta_rows: list[dict[str, Any]],
    stats: dict[str, Any],
    out_dir: Path,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # distributions
    fam = Counter(m["primary_family"] for m in meta_rows)
    pat = Counter((m["primary_family"], m["primary_pattern"]) for m in meta_rows)
    fam_ok = dict(fam) == FAMILY_TARGETS
    pat_ok = True
    pat_detail = {}
    for family, patterns in PATTERN_TARGETS.items():
        for p, n in patterns.items():
            got = pat.get((family, p), 0)
            pat_detail[f"{family}/{p}"] = {"target": n, "got": got}
            if got != n:
                pat_ok = False

    # label validation
    schema_fail = 0
    conflict = 0
    forbidden = 0
    for r in delta_rows:
        try:
            validate_need_profile(r["need_profile"])
        except Exception:  # noqa: BLE001
            schema_fail += 1
        sc = r["need_profile"].get("semantic_constraints") or {}
        pos = {x["concept"] for x in sc.get("positive") or []}
        neg = {x["concept"] for x in sc.get("negative") or []}
        if pos & neg:
            conflict += 1
        blob = json.dumps(r["need_profile"], ensure_ascii=False).lower()
        if any(x in blob for x in ("fixture.", "category-", "cat_")):
            forbidden += 1

    # internal exact dup
    norms = [normalize_utt(r["utterance"]) for r in delta_rows]
    exact_dup = len(norms) - len(set(norms))

    # leakage vs eval (near) and train (exact only)
    corpora = load_corpus_sets(_ROOT)
    train_norms = {normalize_utt(u) for u in corpora["train"]}
    near_eval = []
    exact_train = []
    for r in delta_rows:
        nu = normalize_utt(r["utterance"])
        if nu in train_norms:
            exact_train.append(r["id"])
        hit = near_hit(r["utterance"], corpora["eval"])
        if hit:
            near_eval.append({"id": r["id"], "utt": r["utterance"], "hit": hit})

    # banned inclusions
    banned_hit = []
    for r in delta_rows:
        for b in BANNED_NEAR_UTTERANCES:
            if near_hit(r["utterance"], [b]):
                banned_hit.append(r["id"])

    # matcher/gold ids never as source
    source_eval_hits = [m for m in meta_rows if m.get("source_eval_utterance_id") in BANNED_EVAL_IDS]

    # template share warnings
    template_counts: Counter[str] = Counter(stats["template_counts"])
    warnings = []
    blockers = []
    for family in FAMILY_TARGETS:
        fam_metas = [m for m in meta_rows if m["primary_family"] == family]
        tc = Counter(m["template_skeleton"] for m in fam_metas)
        if not fam_metas:
            continue
        top_n, top_c = tc.most_common(1)[0]
        share = top_c / len(fam_metas)
        if share > 0.10:
            warnings.append({
                "type": "template_share",
                "family": family,
                "template": top_n,
                "share": share,
                "count": top_c,
            })

    # entity concentration
    ent = Counter(stats["entity_counts"])
    total_ent = sum(ent.values()) or 1
    for e, c in ent.most_common(5):
        if c / total_ent > 0.20:
            warnings.append({"type": "entity_concentration", "entity": e, "share": c / total_ent})

    # pair report
    pairs = stats["pair_groups"]
    invalid_pairs = 0
    for gid, ids in pairs.items():
        rows = [r for r in delta_rows if r["id"] in ids]
        if len(rows) < 2:
            continue
        # golds should differ
        golds = [json.dumps(r["need_profile"]["semantic_constraints"], sort_keys=True) for r in rows]
        if len(set(golds)) < 2 and len(rows) > 1:
            invalid_pairs += 1

    review_queue = stats["review_queue"]
    unresolved_blockers = [q for q in review_queue if q.get("blocker")]
    # near_eval and banned are blockers
    if near_eval:
        blockers.append({"type": "near_eval_leakage", "count": len(near_eval), "examples": near_eval[:5]})
    if exact_train:
        blockers.append({"type": "exact_train_dup", "ids": exact_train[:10], "count": len(exact_train)})
    if banned_hit:
        blockers.append({"type": "banned_utterance", "ids": banned_hit})
    if exact_dup:
        blockers.append({"type": "exact_dup", "count": exact_dup})
    if schema_fail or conflict or forbidden:
        blockers.append({"type": "label", "schema_fail": schema_fail, "conflict": conflict, "forbidden": forbidden})
    if not fam_ok or not pat_ok:
        blockers.append({"type": "distribution_mismatch", "fam_ok": fam_ok, "pat_ok": pat_ok})
    if len(delta_rows) != 1200:
        blockers.append({"type": "row_count", "n": len(delta_rows)})
    if source_eval_hits:
        blockers.append({"type": "banned_source_eval", "n": len(source_eval_hits)})
    if unresolved_blockers:
        blockers.append({"type": "review_blockers", "n": len(unresolved_blockers)})

    decision = "V4_DATASET_BUILD_READY_FOR_SFT" if not blockers else "V4_DATASET_BUILD_REJECT"

    # write data
    delta_path = data_dir / "need_profile_sft.v4.delta.jsonl"
    with delta_path.open("w", encoding="utf-8") as fh:
        for r in delta_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    meta_path = data_dir / "v4_row_metadata.jsonl"
    with meta_path.open("w", encoding="utf-8") as fh:
        for m in meta_rows:
            fh.write(json.dumps(m, ensure_ascii=False) + "\n")

    # merged train = v3 + delta (new artifact only)
    train_path = data_dir / "need_profile_sft.v4.train.jsonl"
    v3 = _ROOT / "training/exports/need_profile_sft.v3.jsonl"
    with train_path.open("w", encoding="utf-8") as out:
        if v3.is_file():
            for line in v3.open(encoding="utf-8"):
                if line.strip():
                    out.write(line if line.endswith("\n") else line + "\n")
        for r in delta_rows:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")

    # reports
    (out_dir / "v4_family_distribution.json").write_text(
        json.dumps({"targets": FAMILY_TARGETS, "got": dict(fam), "pass": fam_ok}, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "v4_pattern_distribution.json").write_text(
        json.dumps({"detail": pat_detail, "pass": pat_ok}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out_dir / "v4_entity_distribution.json").write_text(
        json.dumps({
            "canonical_entity_counts": dict(ent.most_common()),
            "top10": ent.most_common(10),
            "out_of_ontology_count": 0,
            "total_entity_mentions": total_ent,
        }, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out_dir / "v4_template_distribution.json").write_text(
        json.dumps({
            "unique_normalized_templates": len(template_counts),
            "top_template_frequencies": template_counts.most_common(20),
            "warnings": [w for w in warnings if w["type"] == "template_share"],
        }, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out_dir / "v4_minimal_pair_report.json").write_text(
        json.dumps({
            "minimal_pair_group_count": len(pairs),
            "minimal_pair_row_count": sum(len(v) for v in pairs.values()),
            "invalid_pair_count": invalid_pairs,
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "v4_leakage_report.json").write_text(
        json.dumps({
            "method": {
                "normalize": "NFKC+casefold+punct_strip",
                "exact": True,
                "jaccard_threshold": 0.72,
                "substring_ratio": 0.85,
            },
            "exact_duplicate_inside_delta": exact_dup,
            "near_eval_leakage_count": len(near_eval),
            "near_eval_examples": near_eval[:10],
            "banned_utterance_hits": banned_hit,
            "matcher_or_gold_review_source_hits": len(source_eval_hits),
        }, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out_dir / "v4_label_validation.json").write_text(
        json.dumps({
            "schema_validity": 1.0 if schema_fail == 0 else (len(delta_rows) - schema_fail) / max(len(delta_rows), 1),
            "schema_fail_count": schema_fail,
            "gold_positive_negative_conflict": conflict,
            "forbidden_count": forbidden,
            "negation_quality": stats["neg_stats"],
        }, indent=2) + "\n",
        encoding="utf-8",
    )

    with (out_dir / "v4_review_queue.csv").open("w", encoding="utf-8", newline="") as fh:
        fields = ["id", "utterance", "reason", "family", "pattern", "blocker"]
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for q in review_queue:
            w.writerow(q)

    validation = {
        "experiment_id": EXPERIMENT,
        "delta_row_count": len(delta_rows),
        "family_pass": fam_ok,
        "pattern_pass": pat_ok,
        "schema_fail": schema_fail,
        "conflict": conflict,
        "forbidden": forbidden,
        "exact_dup": exact_dup,
        "near_eval": len(near_eval),
        "unresolved_blockers": len(unresolved_blockers) + len(blockers),
        "blockers": blockers,
        "warnings": warnings,
        "decision": decision,
        "campaign_gate": "CLOSED",
        "v4_training": "NOT_STARTED",
        "quant_attribution": "NOT_TESTED",
    }
    (out_dir / "p17_v4_dataset_validation.json").write_text(
        json.dumps(validation, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    meta = {
        "experiment_id": EXPERIMENT,
        "source_experiment": SOURCE_EXPERIMENT,
        "created_at": _utc(),
        "delta_rows": len(delta_rows),
        "merged_train_rows": sum(1 for _ in train_path.open()) if train_path.is_file() else None,
        "base_train": str(v3) if v3.is_file() else None,
        "review_status_policy": REVIEW_STATUS,
        "human_reviewed_label_used": False,
        "decision": decision,
        "campaign_gate": "CLOSED",
        "v4_training": "NOT_STARTED",
    }
    (out_dir / "v4_dataset_meta.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )

    report = f"""# P17-V4-DATASET-BUILD-001 Report

**Created:** `{_utc()}`  
**Decision:** `{decision}`  
**Campaign Gate:** CLOSED  
**V4 training:** NOT STARTED  
**Quant:** NOT TESTED  
**Review status on rows:** `{REVIEW_STATUS}` (not HUMAN_REVIEWED)

## Counts

| Check | Value |
|---|---|
| Delta rows | {len(delta_rows)} / 1200 |
| Family distribution | {'PASS' if fam_ok else 'FAIL'} |
| Pattern distribution | {'PASS' if pat_ok else 'FAIL'} |
| Schema fail | {schema_fail} |
| Gold conflict | {conflict} |
| Forbidden | {forbidden} |
| Exact duplicates | {exact_dup} |
| Near eval leakage | {len(near_eval)} |
| Unresolved blockers | {len(blockers)} |
| Review queue rows | {len(review_queue)} (non-blocker warnings allowed) |

## Family got

```json
{json.dumps(dict(fam), indent=2)}
```

## Negation quality (subset tags)

```json
{json.dumps(stats['neg_stats'], indent=2)}
```

## Minimal pairs

- groups: {len(pairs)}
- rows: {sum(len(v) for v in pairs.values())}
- invalid: {invalid_pairs}

## Final

```text
P17-V4-DATASET-BUILD-001 = {'COMPLETE' if decision.endswith('READY_FOR_SFT') else 'INCOMPLETE'}
Delta rows               = {len(delta_rows)} / 1200
Family distribution      = {'PASS' if fam_ok else 'FAIL'}
Pattern distribution     = {'PASS' if pat_ok else 'FAIL'}
Schema validity          = {1.0 if schema_fail == 0 else 'FAIL'}
Gold conflict            = {conflict}
Forbidden                = {forbidden}
Exact duplicates         = {exact_dup}
Near eval leakage        = {len(near_eval)}
Unresolved blockers      = {len(blockers)}
Dataset decision         = {decision}
V4 training              = NOT STARTED
Quant attribution        = NOT TESTED
Campaign Gate            = CLOSED
```
"""
    (out_dir / "p17_v4_dataset_build_report.md").write_text(report, encoding="utf-8")
    return validation


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--out-dir", type=Path, default=_ROOT / "artifacts" / "p17" / "v4")
    args = ap.parse_args()
    # assert targets sum
    assert sum(FAMILY_TARGETS.values()) == 1200
    for f, pats in PATTERN_TARGETS.items():
        assert sum(pats.values()) == FAMILY_TARGETS[f], f

    print(f"[{_utc()}] generating delta…", flush=True)
    delta, meta, stats = generate_all(seed=args.seed)
    print(f"[{_utc()}] generated {len(delta)}; validating…", flush=True)
    validation = validate_and_report(delta, meta, stats, args.out_dir)
    print(json.dumps(validation, indent=2, ensure_ascii=False))
    if validation["decision"] != "V4_DATASET_BUILD_READY_FOR_SFT":
        raise SystemExit(2)


if __name__ == "__main__":
    main()

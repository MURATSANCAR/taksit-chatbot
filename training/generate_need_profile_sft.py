#!/usr/bin/env python3
"""Generate a large, schema-valid NeedProfile SFT set (controlled templates).

Aligned with FAST constraint-boost / HR100: every row carries
``semantic_constraints.{positive,negative,corrections}``.

Does NOT invent merchant/bank/product fixture IDs.
Merges golden + DRAFT HR rows (HUMAN_REVIEWED val held out) with synthetic
hard-neg / clear / clarify / correction diversity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any, Iterator

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from taksitlio.training.export_sft import (  # noqa: E402
    build_sft_row,
    iter_golden_sft_rows,
    iter_hr_validation_sft_rows,
    write_jsonl,
)
from taksitlio.understanding.fast.schema_utils import (  # noqa: E402
    build_empty_need_profile,
    validate_need_profile,
)

CATEGORIES = [
    ("telefon", "MOBILE_PHONE", ["telefon", "cep telefonu", "akıllı telefon"]),
    ("laptop", "LAPTOP", ["laptop", "dizüstü", "notebook", "bilgisayar"]),
    ("tablet", "TABLET", ["tablet", "ipad gibi tablet"]),
    ("televizyon", "TV", ["televizyon", "tv", "smart tv"]),
    ("kulaklık", "HEADPHONES", ["kulaklık", "bluetooth kulaklık"]),
    ("saat", "SMARTWATCH", ["akıllı saat", "saat"]),
    ("beyaz eşya", "APPLIANCE", ["buzdolabı", "çamaşır makinesi", "bulaşık makinesi"]),
    ("süpürge", "VACUUM", ["süpürge", "robot süpürge"]),
    ("mobilya", "FURNITURE", ["koltuk", "masa", "yatak", "tv sehpası"]),
    ("konsol", "CONSOLE", ["oyun konsolu", "playstation", "xbox"]),
    ("kamera", "CAMERA", ["kamera", "aksiyon kamerası"]),
    ("klima", "AC", ["klima", "split klima"]),
]

# Explicit pos/neg pairs in HR style (surface concepts, not fixture keys).
HARD_NEG_PAIRS = [
    ("laptop", "telefon", [
        "telefon istemiyorum, bilgisayar arıyorum",
        "telefon değil laptop bakıyorum",
        "cep telefonu olmasın, laptop lazım",
        "telefon istemiyorum laptop istiyorum",
    ]),
    ("telefon", "tablet", [
        "tablet almam gerekmiyor, telefon arıyorum",
        "tablet değil telefon bakıyorum",
        "tablet istemiyorum telefon lazım",
    ]),
    ("tablet", "laptop", [
        "laptop değil tablet istiyorum",
        "bilgisayar olmasın tablet arıyorum",
        "laptop istemiyorum, tablet bakıyorum",
    ]),
    ("televizyon", "tablet", [
        "tablet değil televizyon istiyorum",
        "tablet olmasın tv bakıyorum",
    ]),
    ("kulaklık", "saat", [
        "saat değil kulaklık arıyorum",
        "akıllı saat istemiyorum kulaklık lazım",
    ]),
    ("süpürge", "klima", [
        "klima değil süpürge bakıyorum",
        "klima istemiyorum robot süpürge lazım",
    ]),
    ("konsol", "laptop", [
        "laptop değil konsol istiyorum",
        "bilgisayar olmasın playstation bakıyorum",
    ]),
    ("kamera", "telefon", [
        "telefon değil kamera arıyorum",
        "cep telefonu istemiyorum aksiyon kamerası lazım",
    ]),
]

CORRECTION_PAIRS = [
    ("telefon", "laptop", [
        "yanlış söyledim telefon değil laptop istiyorum",
        "özür dilerim, telefon demedim laptop lazım",
        "telefon değil aslında laptop bakıyorum",
    ]),
    ("tablet", "telefon", [
        "düzelteyim tablet değil telefon arıyorum",
        "yanlış, tablet değil telefon istiyorum",
    ]),
    ("laptop", "tablet", [
        "bilgisayar demiştim ama tablet istiyorum",
        "laptop değil tablet olsun",
    ]),
]

USAGES = [
    ("education", ["okul", "üniversite", "ders", "ödev"]),
    ("gaming", ["oyun", "gaming", "fps"]),
    ("business", ["iş", "ofis", "toplantı"]),
    ("media", ["film", "dizi", "netflix"]),
    ("home", ["ev", "mutfak", "temizlik"]),
]

PREFS = [
    ("lightweight", ["hafif", "taşınabilir"]),
    ("longevity", ["uzun ömürlü", "dayanıklı"]),
    ("camera_quality", ["kamerası iyi", "kamerası sağlam"]),
    ("performance", ["güçlü", "hızlı", "performanslı"]),
    ("quiet", ["sessiz"]),
    ("installment", ["taksitli", "aylık ödeme"]),
]

BUDGETS = [
    ("APPROXIMATE", 15000, None, None, ["{n} bin civarı", "yaklaşık {n} bin", "{n} bin fln"]),
    ("APPROXIMATE", 25000, None, None, ["{n} bin gibi", "{n} bin bandında"]),
    ("APPROXIMATE", 40000, None, None, ["{n} bin civarı", "bütçe {n} bin"]),
    ("EXACT", 29999, None, None, ["tam {n} TL", "bütçem {n}"]),
    ("RANGE", None, 20000, 35000, ["{min}-{max} bin arası", "{min} ile {max} bin arasında"]),
    ("RANGE", None, 30000, 50000, ["{min}-{max} arası"]),
    ("MONTHLY_PAYMENT", None, None, None, ["aylık {m} bin", "ayda {m} bin ödeyebilirim"]),
]

MONTHLY = [1, 2, 3, 4, 5]
TERMS = [6, 9, 12, 18, 24]

CLARIFY_UTTERANCES = [
    "Okul için bir şey lazım",
    "Bir şey alacağım ama ne alacağımı bilmiyorum",
    "Elektronik bakıyorum",
    "Ev için lazım",
    "Hediye arıyorum",
    "Uygun bir şey öner",
    "Taksitli bir şey istiyorum",
    "Bütçem var ama ürün net değil",
]

ATTR_NEGATIONS = [
    ("apple", ["Apple olmasın", "iphone istemiyorum"]),
    ("gaming", ["oyun için olmasın", "gaming istemiyorum"]),
    ("heavy", ["ağır olmasın"]),
    ("used", ["ikinci el olmasın"]),
]


def _budget_phrase(
    kind: str,
    value: int | None,
    minimum: int | None,
    maximum: int | None,
    monthly: int | None,
    templates: list[str],
    rng: random.Random,
) -> str:
    tmpl = rng.choice(templates)
    n = (value or maximum or minimum or 20000) // 1000
    mn = (minimum or 20000) // 1000
    mx = (maximum or 35000) // 1000
    m = monthly or 2
    return tmpl.format(n=n, min=mn, max=mx, m=m)


def _sc_item(concept: str, provenance: str, weight: float = 0.9) -> dict[str, Any]:
    return {"concept": concept, "provenance": provenance, "weight": weight}


def _profile(
    *,
    utterance: str,
    intent: str = "PRODUCT_PURCHASE",
    category_label: str | None = None,
    category_hint: str | None = None,
    usage: str | None = None,
    prefs: list[str] | None = None,
    budget: dict[str, Any] | None = None,
    clarify: bool = False,
    clarify_intent: str | None = None,
    positive: list[str] | None = None,
    negative: list[str] | None = None,
    corrections: list[dict[str, Any]] | None = None,
    confidence: float = 0.85,
) -> dict[str, Any]:
    profile = build_empty_need_profile(
        utterance=utterance, intent=intent, confidence=confidence
    )
    if budget:
        profile["budget"] = budget
    pref_list: list[dict[str, Any]] = []
    pos_items: list[dict[str, Any]] = []
    for c in positive or ([] if not category_label else [category_label]):
        pos_items.append(_sc_item(c, "EXPLICIT", 0.95))
        pref_list.append({"concept": c, "importance": 0.9})
    if category_hint:
        pref_list.append({"concept": f"category_hint:{category_hint}", "importance": 0.7})
    for p in prefs or []:
        pref_list.append({"concept": p, "importance": 0.8})
    profile["preferences"] = pref_list
    if usage:
        profile["usage_context"] = [usage]
    neg_items = [_sc_item(c, "EXPLICIT_NEGATION", 0.99) for c in (negative or [])]
    corr_items = [
        _sc_item(str(corr["concept"]), "USER_CORRECTION", float(corr.get("weight") or 0.9))
        for corr in (corrections or [])
    ]
    if clarify:
        profile["clarification"] = {
            "required": True,
            "question_intent": clarify_intent or "category",
        }
        profile["ambiguities"] = [
            {"code": "ambiguous_category", "description": "ambiguous_or_multiple"}
        ]
        profile["confidence"] = min(confidence, 0.55)
    profile["semantic_constraints"] = {
        "positive": pos_items,
        "negative": neg_items,
        "corrections": corr_items,
    }
    validate_need_profile(profile)
    return profile


def iter_synthetic(target: int, *, seed: int = 42) -> Iterator[dict[str, Any]]:
    rng = random.Random(seed)
    produced = 0

    def emit(case_id: str, utterance: str, profile: dict[str, Any]) -> dict[str, Any]:
        return build_sft_row(
            case_id=case_id,
            utterance=utterance,
            need_profile=profile,
            source_path="training/generate_need_profile_sft.py",
            annotation_status="SYNTHETIC",
            split="train",
        )

    # Hard neg / sibling exclusion — primary HR100 failure mode.
    hard_n = int(target * 0.35)
    for i in range(hard_n):
        pos, neg, templates = HARD_NEG_PAIRS[i % len(HARD_NEG_PAIRS)]
        base = templates[i % len(templates)]
        utterance = f"{base} (#{i})"
        hint = next((h for lab, h, _ in CATEGORIES if lab == pos), None)
        profile = _profile(
            utterance=utterance,
            category_label=pos,
            category_hint=hint,
            positive=[pos],
            negative=[neg],
            confidence=0.9,
        )
        yield emit(f"syn-hardneg-{i}", utterance, profile)
        produced += 1

    clear_n = int(target * 0.30)
    for i in range(clear_n):
        cat_label, hint, aliases = CATEGORIES[i % len(CATEGORIES)]
        alias = aliases[i % len(aliases)]
        kind, value, minimum, maximum, templates = BUDGETS[i % len(BUDGETS)]
        monthly = MONTHLY[i % len(MONTHLY)] if kind == "MONTHLY_PAYMENT" else None
        if kind == "RANGE":
            budget = {
                "type": "RANGE",
                "value": None,
                "minimum": minimum,
                "maximum": maximum,
                "monthly_payment": None,
                "currency": "TRY",
            }
        elif kind == "MONTHLY_PAYMENT":
            budget = {
                "type": "MONTHLY_PAYMENT",
                "value": None,
                "minimum": None,
                "maximum": None,
                "monthly_payment": float((monthly or 2) * 1000),
                "currency": "TRY",
            }
        elif kind == "EXACT":
            budget = {
                "type": "EXACT",
                "value": float(value or 29999),
                "minimum": None,
                "maximum": None,
                "monthly_payment": None,
                "currency": "TRY",
            }
        else:
            base = int(value or 20000)
            jitter = (i % 7) * 1000
            budget = {
                "type": "APPROXIMATE",
                "value": float(base + jitter),
                "minimum": None,
                "maximum": None,
                "monthly_payment": None,
                "currency": "TRY",
            }
        bphrase = _budget_phrase(
            kind,
            int(budget["value"]) if budget["value"] is not None else value,
            minimum,
            maximum,
            monthly,
            templates,
            rng,
        )
        pref_key, pref_words = PREFS[i % len(PREFS)]
        usage_key, usage_words = USAGES[i % len(USAGES)]
        use_usage = i % 2 == 0
        use_pref = i % 3 != 0
        use_term = i % 4 == 0
        verb = ["bakıyorum", "arıyorum", "alacağım", "lazım"][i % 4]
        parts = [f"{alias} {verb}"]
        if use_usage:
            parts.append(f"{usage_words[i % len(usage_words)]} için")
        if use_pref:
            parts.append(pref_words[i % len(pref_words)])
        parts.append(bphrase)
        if use_term:
            parts.append(f"{TERMS[i % len(TERMS)]} ay")
        utterance = f"{', '.join(parts)} (#{i})"
        prefs = [pref_key] if use_pref else []
        if use_term or pref_key == "installment":
            prefs = list(dict.fromkeys(prefs + ["installment"]))
        # Optional attribute negation (not sibling category).
        neg: list[str] = []
        if i % 5 == 0:
            neg_key, neg_phrases = ATTR_NEGATIONS[i % len(ATTR_NEGATIONS)]
            utterance = f"{utterance.rstrip()} ama {neg_phrases[i % len(neg_phrases)]}"
            neg = [neg_key]
        profile = _profile(
            utterance=utterance,
            category_label=cat_label,
            category_hint=hint,
            usage=usage_key if use_usage else None,
            prefs=prefs,
            budget=budget,
            positive=[cat_label],
            negative=neg,
            confidence=0.88,
        )
        yield emit(f"syn-clear-{i}", utterance, profile)
        produced += 1

    corr_n = int(target * 0.12)
    for i in range(corr_n):
        pos, prev, templates = CORRECTION_PAIRS[i % len(CORRECTION_PAIRS)]
        base = templates[i % len(templates)]
        utterance = f"{base} (#{i})"
        hint = next((h for lab, h, _ in CATEGORIES if lab == pos), None)
        profile = _profile(
            utterance=utterance,
            category_label=pos,
            category_hint=hint,
            positive=[pos],
            negative=[prev],
            corrections=[{"concept": pos, "weight": 0.95}],
            confidence=0.9,
        )
        yield emit(f"syn-corr-{i}", utterance, profile)
        produced += 1

    clarify_n = int(target * 0.15)
    for i in range(clarify_n):
        if i % 2 == 0:
            usage_key, usage_words = USAGES[i % len(USAGES)]
            utterance = (
                f"{usage_words[i % len(usage_words)].capitalize()} için bir şey lazım (#{i})"
            )
            usage = usage_key
        else:
            utterance = f"{CLARIFY_UTTERANCES[i % len(CLARIFY_UTTERANCES)]} (#{i})"
            usage = None
        profile = _profile(
            utterance=utterance,
            clarify=True,
            clarify_intent="category",
            usage=usage,
            positive=[],
            confidence=0.5,
        )
        yield emit(f"syn-clarify-{i}", utterance, profile)
        produced += 1

    extras = [
        ("Şu iki laptopu karşılaştırmak istiyorum", "COMPARE_OPTIONS", ["laptop"], []),
        ("12 ay taksit imkanı var mı genel olarak", "INSTALLMENT_INQUIRY", [], []),
        ("Hava durumu nasıl yarın", "OUT_OF_SCOPE", [], []),
        ("Kredi kartı limitimi öğrenmek istiyorum", "OUT_OF_SCOPE", [], []),
        ("Bütçem ne kadar olmalı bilmiyorum", "BUDGET_INQUIRY", [], []),
    ]
    i = 0
    while produced < target:
        utterance_base, intent, pos, neg = extras[i % len(extras)]
        utterance = f"{utterance_base} (#{i})"
        profile = _profile(
            utterance=utterance,
            intent=intent,
            positive=pos,
            negative=neg,
            confidence=0.9 if intent == "OUT_OF_SCOPE" else 0.75,
        )
        yield emit(f"syn-extra-{i}", utterance, profile)
        produced += 1
        i += 1


def _dedupe_key(row: dict[str, Any]) -> str:
    u = str(row.get("utterance") or "").strip().casefold()
    return hashlib.sha1(u.encode("utf-8")).hexdigest()


def _upsample(rows: list[dict[str, Any]], *, times: int) -> list[dict[str, Any]]:
    if times <= 1 or not rows:
        return list(rows)
    out: list[dict[str, Any]] = []
    for t in range(times):
        for row in rows:
            cloned = dict(row)
            if t > 0:
                cloned["id"] = f"{row.get('id')}__up{t}"
            out.append(cloned)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate expanded NeedProfile SFT JSONL")
    parser.add_argument("--target", type=int, default=2500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--out",
        type=Path,
        default=_ROOT / "training" / "exports" / "need_profile_sft.v3.jsonl",
    )
    parser.add_argument("--no-seed-export", action="store_true")
    parser.add_argument(
        "--include-hr-eval",
        action="store_true",
        help="Also include HUMAN_REVIEWED val rows (leaks into HR100; off by default)",
    )
    parser.add_argument(
        "--draft-upsample",
        type=int,
        default=4,
        help="Repeat DRAFT HR rows this many times (constraint-rich)",
    )
    args = parser.parse_args(argv)

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    if not args.no_seed_export:
        seeds: list[dict[str, Any]] = list(iter_golden_sft_rows())
        # Train on DRAFT only by default — hold out HUMAN_REVIEWED val for HR100.
        drafts = list(
            iter_hr_validation_sft_rows(
                human_reviewed_only=False,
                draft_only=True,
            )
        )
        seeds.extend(_upsample(drafts, times=max(1, args.draft_upsample)))
        if args.include_hr_eval:
            seeds.extend(
                iter_hr_validation_sft_rows(human_reviewed_only=True)
            )
        for row in seeds:
            # Never train on held-out eval split unless explicitly included.
            if row.get("split") == "eval" and not args.include_hr_eval:
                continue
            k = _dedupe_key(row)
            if k in seen and "__up" not in str(row.get("id")):
                continue
            seen.add(k if "__up" not in str(row.get("id")) else f"{k}:{row.get('id')}")
            rows.append(row)

    need = max(0, args.target - len(rows))
    for row in iter_synthetic(need, seed=args.seed):
        k = _dedupe_key(row)
        if k in seen:
            continue
        seen.add(k)
        rows.append(row)
        if len(rows) >= args.target:
            break

    kept: list[dict[str, Any]] = []
    for row in rows:
        try:
            validate_need_profile(row["need_profile"])
            kept.append(row)
        except Exception as exc:  # noqa: BLE001
            print(f"drop invalid: {row.get('id')}: {exc}", file=sys.stderr)

    n = write_jsonl(kept, args.out)
    with_sc = sum(
        1 for r in kept if (r["need_profile"].get("semantic_constraints") or {}).get("positive")
        is not None
    )
    with_neg = sum(
        1
        for r in kept
        if (r["need_profile"].get("semantic_constraints") or {}).get("negative")
    )
    clarify = sum(
        1 for r in kept if (r["need_profile"].get("clarification") or {}).get("required")
    )
    print(
        json.dumps(
            {
                "wrote": n,
                "out": str(args.out),
                "with_semantic_constraints_field": with_sc,
                "with_negative_constraints": with_neg,
                "clarification_required": clarify,
                "synthetic": sum(1 for r in kept if r.get("annotation_status") == "SYNTHETIC"),
                "draft": sum(1 for r in kept if r.get("annotation_status") == "DRAFT"),
                "human_reviewed": sum(
                    1 for r in kept if r.get("annotation_status") == "HUMAN_REVIEWED"
                ),
            },
            ensure_ascii=False,
        )
    )
    print(
        "NOTE: v3 constraint-aligned SFT — no QUALITY_READY claim; HR val held out.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

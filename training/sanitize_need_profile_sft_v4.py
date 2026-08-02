#!/usr/bin/env python3
"""P17-V4-DATASET-SANITIZE-001 — pre-SFT semantic cleanup.

Does NOT train. Campaign Gate stays CLOSED.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from taksitlio.training.export_sft import DEFAULT_SYSTEM_PROMPT, build_sft_row  # noqa: E402
from taksitlio.understanding.fast.schema_utils import (  # noqa: E402
    build_empty_need_profile,
    validate_need_profile,
)

EXPERIMENT = "P17-V4-DATASET-SANITIZE-001"
SOURCE_BUILD = "P17-V4-DATASET-BUILD-001"
REVIEW_STATUS = "CURSOR_GENERATED_VALIDATED"

FAMILY_TARGETS = {
    "CORRECTION": 360,
    "NEGATION_HARD_NEGATIVE": 300,
    "POSITIVE_MISS_EMPTY": 240,
    "OVER_EXTRACTION_SUPPRESSION": 180,
    "CONFLICT_PREVENTION": 60,
    "AMBIGUOUS_EXPECT_EMPTY": 60,
}
PATTERN_TARGETS = {
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
    "AMBIGUOUS_EXPECT_EMPTY": {"AMBIGUOUS_EXPECT_EMPTY": 60},
}

ENTITIES: list[tuple[str, list[str]]] = [
    ("telefon", ["telefon", "cep telefonu", "akıllı telefon", "tlfn", "mobil"]),
    ("laptop", ["laptop", "dizüstü", "notebook", "dizustu bilgisayar"]),
    ("tablet", ["tablet", "tabletim"]),
    ("televizyon", ["televizyon", "tv", "smart tv"]),
    ("kulaklık", ["kulaklık", "bluetooth kulaklık", "kulak lık"]),
    ("akıllı saat", ["akıllı saat", "saat", "smartwatch"]),
    ("buzdolabı", ["buzdolabı", "buzdolabi"]),
    ("çamaşır makinesi", ["çamaşır makinesi", "camasir makinesi"]),
    ("bulaşık makinesi", ["bulaşık makinesi", "bulasik makinesi"]),
    ("süpürge", ["süpürge", "robot süpürge", "dikey süpürge"]),
    ("klima", ["klima", "split klima", "duvar tipi klima"]),
    ("vantilatör", ["vantilatör", "fan", "vantilator"]),
    ("oyun konsolu", ["oyun konsolu", "playstation", "xbox", "konsol"]),
    ("kamera", ["kamera", "aksiyon kamerası"]),
    ("monitör", ["monitör", "monitor"]),
    ("masaüstü", ["masaüstü", "masaüstü bilgisayar", "pc"]),
    ("yazıcı", ["yazıcı", "yazici", "lazer yazıcı"]),
    ("mouse", ["mouse", "fare"]),
    ("klavye", ["klavye", "mekanik klavye"]),
    ("hoparlör", ["hoparlör", "bluetooth hoparlör", "ses sistemi"]),
    ("e-bike", ["e-bike", "elektrikli bisiklet"]),
    ("bisiklet", ["bisiklet", "dağ bisikleti"]),
    ("ütü", ["ütü", "buharlı ütü"]),
    ("mikrodalga", ["mikrodalga"]),
    ("airfryer", ["airfryer", "yağsız fritöz"]),
    ("kahve makinesi", ["kahve makinesi", "espresso makinesi"]),
    ("koltuk", ["koltuk", "kanepe"]),
    ("matras", ["yatak", "ortopedik yatak"]),
]

PAIRS = [
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

BUDGETS = [15000, 20000, 25000, 30000, 35000, 40000, 50000, 60000, 80000]
ARTIFICIAL_RE = re.compile(
    r"(?:·\s*)?v4-(?:cor|neg|pos|ove|con|amb)[a-z]*\d*-\d+|v4empty-\d+|·\s*v4-[a-z0-9-]+",
    re.I,
)
BANNED_NEAR = [
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
    t = ARTIFICIAL_RE.sub(" ", t)
    t = re.sub(r"\(#\d+\)", " ", t)
    t = re.sub(r"[^\w\sçğıöşü]", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def tokens(text: str) -> set[str]:
    return {w for w in normalize_utt(text).split() if len(w) > 1}


def jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


class EvalIndex:
    """Precomputed exact/near corpus for leakage checks."""

    def __init__(self, corpus: list[str], *, jacc_th: float = 0.72) -> None:
        self.jacc_th = jacc_th
        self.exact: set[str] = set()
        self.entries: list[tuple[str, set[str]]] = []
        seen: set[str] = set()
        for b in corpus:
            nb = normalize_utt(b)
            if not nb or nb in seen:
                continue
            seen.add(nb)
            self.exact.add(nb)
            self.entries.append((nb, {w for w in nb.split() if len(w) > 1}))

    def hit(self, utt: str) -> Optional[str]:
        nu = normalize_utt(utt)
        if not nu:
            return None
        if nu in self.exact:
            return f"exact:{nu[:60]}"
        ta = {w for w in nu.split() if len(w) > 1}
        if not ta:
            return None
        for nb, tb in self.entries:
            if len(nu) >= 12 and (nu in nb or nb in nu):
                if min(len(nu), len(nb)) / max(len(nu), len(nb)) > 0.85:
                    return f"substring:{nb[:60]}"
            if not tb:
                continue
            inter = len(ta & tb)
            if inter and inter / len(ta | tb) >= self.jacc_th:
                return f"jaccard:{nb[:60]}"
        return None


def near_hit(utt: str, corpus: list[str] | EvalIndex, *, jacc_th: float = 0.72) -> Optional[str]:
    if isinstance(corpus, EvalIndex):
        return corpus.hit(utt)
    return EvalIndex(corpus, jacc_th=jacc_th).hit(utt)


def _sc(concept: str, provenance: str, weight: float) -> dict[str, Any]:
    return {"concept": concept, "provenance": provenance, "weight": weight}


def _aliases(canon: str) -> list[str]:
    for c, als in ENTITIES:
        if c == canon:
            return list(als)
    return [canon]


def _surface(rng: random.Random, canon: str, i: int) -> str:
    als = _aliases(canon)
    return als[(i + rng.randint(0, len(als) - 1)) % len(als)]


def strip_category_hints(profile: dict[str, Any]) -> dict[str, Any]:
    prefs = profile.get("preferences") or []
    profile["preferences"] = [
        p for p in prefs if not str(p.get("concept", "")).startswith("category_hint:")
    ]
    return profile


def rebuild_profile(
    *,
    utterance: str,
    positive: list[str],
    negative: list[str],
    corrections: list[str],
    intent: str = "PRODUCT_PURCHASE",
    budget: Optional[dict[str, Any]] = None,
    clarify: bool = False,
    confidence: float = 0.9,
) -> dict[str, Any]:
    if set(positive) & set(negative):
        raise ValueError(f"conflict {set(positive)&set(negative)} in {utterance}")
    profile = build_empty_need_profile(utterance=utterance, intent=intent)
    profile["need_description"] = utterance[:120]
    profile["preferences"] = [{"concept": c, "importance": 0.9} for c in positive]
    profile["semantic_constraints"] = {
        "positive": [_sc(c, "EXPLICIT", 0.95) for c in positive],
        "negative": [_sc(c, "EXPLICIT_NEGATION", 0.99) for c in negative],
        "corrections": [_sc(c, "USER_CORRECTION", 0.95) for c in corrections],
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
    strip_category_hints(profile)
    validate_need_profile(profile)
    return profile


def refresh_messages(row: dict[str, Any]) -> dict[str, Any]:
    assistant = json.dumps(row["need_profile"], ensure_ascii=False, separators=(",", ":"))
    row["messages"] = [
        {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
        {"role": "user", "content": row["utterance"]},
        {"role": "assistant", "content": assistant},
    ]
    return row


def looks_like_correction_utt(utt: str) -> bool:
    """True correction surface — not soft preference / negation-of-negation."""
    u = utt.casefold()
    # Soft preference / hedge uses of "değil" are NOT corrections
    soft_markers = (
        "önceliğim değil",
        "onceligim degil",
        "şart değil",
        "sart degil",
        "kötü demiyorum",
        "kotu demiyorum",
        "olmasa da olur",
        "belki sonra",
        "acil değil",
        "net değil",
        "hiçbir şey net değil",
        "emin değil",
        "karar değil",
    )
    if any(m in u for m in soft_markers):
        return False
    if ("demedim" in u or "demiyorum" in u) and "değil" not in u and "degil" not in u:
        return False
    # X değil Y product correction — require a verb/intent cue after Y, or trailing product pair
    if re.search(
        r"\b[\wçğıöşü]+(?:\s+[\wçğıöşü]+)?\s+değil\s+[\wçğıöşü]+(?:\s+[\wçğıöşü]+)?\s+"
        r"(?:istiyorum|bakıyorum|arıyorum|lazım|olsun|alacağız|karar)",
        u,
    ):
        return True
    if re.search(r"\bdeğil\s+[\wçğıöşü]+(?:\s+[\wçğıöşü]+)?\s*(?:istiyorum|bakıyorum|arıyorum|lazım)", u):
        return True
    return any(
        k in u
        for k in (
            "vazgeç",
            "yanlış söyled",
            "yanlış anlaşılmasın",
            "özür",
            "düzelteyim",
            "boşver",
            "fikrimi değiş",
            " yerine ",
        )
    )


CONTEXTS = [
    "acil",
    "bu hafta",
    "hediye için",
    "ev için",
    "iş için",
    "ofise",
    "yeni taşındık",
    "kampanya döneminde",
    "taksitle",
    "ikinci el değil",
    "online bakıyorum",
    "mağazadan",
    "ailenin kullanımı için",
    "öğrenci için",
    "yazlık için",
    "kışa hazırlık",
    "uzun vadeli",
    "pratik kullanım",
    "sessiz çalışan",
    "enerji verimli",
]

FRAMES = [
    "{ctx}: {body}",
    "{body}, {ctx}",
    "{body}",
    "şöyle söyleyeyim, {body}",
    "netleştireyim: {body}",
    "kısaca {body}",
    "{body} lütfen",
    "bize {body}",
    "bugünlük karar: {body}",
    "tercihimiz şu: {body}",
]


def repair_syn_corr(row: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Swap inverted syn-corr labels to match X değil Y → pos=Y neg=X."""
    sc = row["need_profile"]["semantic_constraints"]
    pos = [x["concept"] for x in sc.get("positive") or []]
    neg = [x["concept"] for x in sc.get("negative") or []]
    # Inverted generator: first tuple was treated as pos but is reject
    new_pos, new_neg = neg, pos
    if not new_pos and not new_neg:
        return row, "drop_empty"
    if set(new_pos) & set(new_neg):
        return row, "drop_conflict"
    profile = rebuild_profile(
        utterance=re.sub(r"\s*\(#\d+\)\s*", " ", row["utterance"]).strip(),
        positive=new_pos,
        negative=new_neg,
        corrections=list(new_pos),
        intent=(row["need_profile"].get("intent") or {}).get("type") or "PRODUCT_PURCHASE",
        budget=row["need_profile"].get("budget"),
        clarify=bool((row["need_profile"].get("clarification") or {}).get("required")),
        confidence=float(row["need_profile"].get("confidence") or 0.9),
    )
    out = dict(row)
    out["utterance"] = profile["need_description"]
    out["need_profile"] = profile
    out["annotation_status"] = "SYNTHETIC_REPAIRED"
    refresh_messages(out)
    return out, "repaired"


def clean_base(v3_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = [json.loads(l) for l in v3_path.open() if l.strip()]
    excl = Counter()
    kept: list[dict[str, Any]] = []
    repaired_corr = 0
    dropped_corr = 0

    for r in rows:
        path = str((r.get("source_ref") or {}).get("path") or "")
        rid = str(r.get("id") or "")
        ann = r.get("annotation_status")
        split = r.get("split")

        is_eval_path = "/evaluation/" in path or "/validation/" in path
        is_upsample = "__up" in rid
        if split and split != "train":
            excl["split_not_train"] += 1
            if is_eval_path or is_upsample:
                excl["eval_source_path"] += 1
            continue
        if ann == "DRAFT":
            excl["draft"] += 1
            if is_eval_path or is_upsample:
                excl["eval_source_path"] += 1
            continue
        if is_eval_path:
            excl["eval_source_path"] += 1
            continue
        if is_upsample:
            excl["upsample"] += 1
            excl["eval_source_path"] += 1
            continue

        # repair syn-corr
        if rid.startswith("syn-corr"):
            fixed, status = repair_syn_corr(r)
            if status.startswith("drop"):
                excl[f"corr_{status}"] += 1
                dropped_corr += 1
                continue
            r = fixed
            repaired_corr += 1
        else:
            # strip category hints; ensure correction rows have corrections[]
            prof = dict(r["need_profile"])
            strip_category_hints(prof)
            sc = prof.get("semantic_constraints") or {}
            utt = r["utterance"]
            if looks_like_correction_utt(utt) and not (sc.get("corrections") or []):
                pos = [x["concept"] for x in sc.get("positive") or []]
                neg = [x["concept"] for x in sc.get("negative") or []]
                if pos and neg and not (set(pos) & set(neg)):
                    prof = rebuild_profile(
                        utterance=re.sub(r"\s*\(#\d+\)\s*", " ", utt).strip(),
                        positive=pos,
                        negative=neg,
                        corrections=list(pos),
                        intent=(prof.get("intent") or {}).get("type") or "PRODUCT_PURCHASE",
                        budget=prof.get("budget"),
                        clarify=bool((prof.get("clarification") or {}).get("required")),
                        confidence=float(prof.get("confidence") or 0.9),
                    )
                    repaired_corr += 1
                else:
                    excl["corr_unrepairable"] += 1
                    dropped_corr += 1
                    continue
            else:
                # still strip hints / validate
                try:
                    validate_need_profile(prof)
                except Exception:  # noqa: BLE001
                    excl["schema_fail_base"] += 1
                    continue
            r = dict(r)
            r["need_profile"] = prof
            r["utterance"] = (prof.get("need_description") or r["utterance"])[:200]
            refresh_messages(r)

        # final guards
        sc = r["need_profile"]["semantic_constraints"]
        pos = {x["concept"] for x in sc.get("positive") or []}
        neg = {x["concept"] for x in sc.get("negative") or []}
        if pos & neg:
            excl["pos_neg_conflict"] += 1
            continue
        if any(str(p.get("concept", "")).startswith("category_hint:") for p in (r["need_profile"].get("preferences") or [])):
            excl["category_hint_remaining"] += 1
            continue
        if ARTIFICIAL_RE.search(r["utterance"]):
            excl["artificial_marker"] += 1
            continue
        kept.append(r)

    report = {
        "input_rows": len(rows),
        "kept_rows": len(kept),
        "exclusions": dict(excl),
        "repaired_correction_rows": repaired_corr,
        "dropped_correction_rows": dropped_corr,
        "removed_draft": excl.get("draft", 0),
        "removed_eval_source": excl.get("eval_source_path", 0),
        "removed_upsample_ids": excl.get("upsample", 0),
    }
    return kept, report


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
    return {
        "type": "RANGE",
        "value": None,
        "minimum": None,
        "maximum": float(value),
        "monthly_payment": None,
        "currency": "TRY",
    }


def make_row(
    *,
    case_id: str,
    utterance: str,
    positive: list[str],
    negative: list[str],
    corrections: list[str],
    family: str,
    pattern: str,
    pair_group_id: Optional[str],
    difficulty: str,
    secondary: list[str],
    neg_subtype: Optional[str],
    intent: str = "PRODUCT_PURCHASE",
    budget: Optional[dict[str, Any]] = None,
    clarify: bool = False,
    confidence: float = 0.9,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if ARTIFICIAL_RE.search(utterance):
        raise ValueError(f"artificial marker in utt: {utterance}")
    profile = rebuild_profile(
        utterance=utterance,
        positive=positive,
        negative=negative,
        corrections=corrections,
        intent=intent,
        budget=budget,
        clarify=clarify,
        confidence=confidence,
    )
    row = build_sft_row(
        case_id=case_id,
        utterance=utterance,
        need_profile=profile,
        source_path="training/sanitize_need_profile_sft_v4.py",
        annotation_status=REVIEW_STATUS,
        split="train",
    )
    meta = {
        "id": case_id,
        "split": "train",
        "review_status": REVIEW_STATUS,
        "experiment_id": EXPERIMENT,
        "source_build": SOURCE_BUILD,
        "primary_family": family,
        "primary_pattern": pattern,
        "secondary_patterns": secondary,
        "pair_group_id": pair_group_id,
        "derived_from_eval_pattern": True,
        "source_eval_utterance_id": None,
        "generation_source": "sanitized_synthetic",
        "difficulty": difficulty,
        "neg_subtype": neg_subtype,
    }
    return row, meta


def _frame(rng: random.Random, body: str, i: int) -> str:
    ctx = CONTEXTS[(i + rng.randint(0, len(CONTEXTS) - 1)) % len(CONTEXTS)]
    frm = FRAMES[(i + rng.randint(0, len(FRAMES) - 1)) % len(FRAMES)]
    return frm.format(ctx=ctx, body=body)


def generate_clean_delta(
    seed: int = 19,
    *,
    eval_utts: Optional[list[str]] = None,
    base_norms: Optional[set[str]] = None,
    eval_index: Optional[EvalIndex] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    rng = random.Random(seed)
    quotas: dict[tuple[str, str], int] = {}
    for fam, pats in PATTERN_TARGETS.items():
        for p, n in pats.items():
            quotas[(fam, p)] = n

    delta: list[dict[str, Any]] = []
    metas: list[dict[str, Any]] = []
    used_norm: set[str] = set(base_norms or set())
    idx = eval_index or EvalIndex(list(eval_utts or BANNED_NEAR))
    pair_groups: dict[str, list[str]] = defaultdict(list)
    neg_stats: Counter[str] = Counter()
    pair_i = 0
    seq = 0

    def remaining(fam: str, pat: str) -> int:
        return quotas.get((fam, pat), 0)

    def take(fam: str, pat: str) -> bool:
        if quotas.get((fam, pat), 0) <= 0:
            return False
        quotas[(fam, pat)] -= 1
        return True

    def commit(row: dict[str, Any], meta: dict[str, Any], *, neg_subtype: Optional[str] = None) -> bool:
        nu = normalize_utt(row["utterance"])
        if not nu or nu in used_norm:
            return False
        if idx.hit(row["utterance"]):
            return False
        used_norm.add(nu)
        delta.append(row)
        metas.append(meta)
        if meta.get("pair_group_id"):
            pair_groups[meta["pair_group_id"]].append(meta["id"])
        if neg_subtype:
            neg_stats[neg_subtype] += 1
        return True

    # --- Phase A: real minimal-pair groups (size 3-5) consuming mixed quotas ---
    while sum(quotas.values()) > 200:
        reject, want = PAIRS[pair_i % len(PAIRS)]
        if rng.random() < 0.5:
            reject, want = want, reject
        pair_i += 1
        pair_i_local = pair_i
        sr, sw = _surface(rng, reject, pair_i), _surface(rng, want, pair_i + 1)
        gid = f"P17V4S-PAIR-{pair_i:06d}"

        candidates: list[tuple[str, str, dict[str, Any]]] = []
        # CORRECTION_X_NOT_Y
        if remaining("CORRECTION", "CORRECTION_X_NOT_Y") > 0:
            candidates.append((
                "CORRECTION", "CORRECTION_X_NOT_Y",
                {"utt": f"{sr} değil {sw} istiyorum", "pos": [want], "neg": [reject], "corr": [want], "neg_sub": "true_negative"},
            ))
        if remaining("CORRECTION", "CORRECTION_RETRACTION") > 0:
            candidates.append((
                "CORRECTION", "CORRECTION_RETRACTION",
                {"utt": f"{sr}ten vazgeçtim, {sw} bakıyorum", "pos": [want], "neg": [reject], "corr": [want], "neg_sub": "true_negative"},
            ))
        if remaining("NEGATION_HARD_NEGATIVE", "NEG_SIMPLE") > 0:
            candidates.append((
                "NEGATION_HARD_NEGATIVE", "NEG_SIMPLE",
                {"utt": f"{sr} istemiyorum, {sw} arıyorum", "pos": [want], "neg": [reject], "corr": [], "neg_sub": "true_negative"},
            ))
        if remaining("CORRECTION", "NEGATION_OF_NEGATION") > 0:
            candidates.append((
                "CORRECTION", "NEGATION_OF_NEGATION",
                {"utt": f"{sw} istemiyorum demedim", "pos": [want], "neg": [], "corr": [], "neg_sub": "negation_of_negation"},
            ))
        if remaining("POSITIVE_MISS_EMPTY", "DIRECT_POSITIVE") > 0:
            candidates.append((
                "POSITIVE_MISS_EMPTY", "DIRECT_POSITIVE",
                {"utt": f"{sw} bakıyorum", "pos": [want], "neg": [], "corr": [], "neg_sub": None},
            ))
        if remaining("CONFLICT_PREVENTION", "NEG_SIMPLE") > 0:
            candidates.append((
                "CONFLICT_PREVENTION", "NEG_SIMPLE",
                {"utt": f"{sw} istiyorum {sr} istemiyorum", "pos": [want], "neg": [reject], "corr": [], "neg_sub": "true_negative"},
            ))
        if remaining("NEGATION_HARD_NEGATIVE", "SOFT_PREFERENCE_NOT_NEGATIVE") > 0:
            candidates.append((
                "NEGATION_HARD_NEGATIVE", "SOFT_PREFERENCE_NOT_NEGATIVE",
                {"utt": f"{sr} önceliğim değil, {sw} tercih ederim", "pos": [want], "neg": [], "corr": [], "neg_sub": "soft_preference"},
            ))

        rng.shuffle(candidates)
        if len(candidates) < 2:
            break
        hi = min(5, len(candidates))
        lo = min(3, hi)
        picked = candidates[: rng.randint(lo, hi)]
        if len(picked) < 2:
            break
        # ensure unique utterances in group
        group_ok = []
        for fam, pat, spec in picked:
            if not take(fam, pat):
                continue
            seq += 1
            utt = _frame(rng, spec["utt"], seq)
            row, meta = make_row(
                case_id=f"p17v4s-{fam[:3].lower()}-{pat[:12].lower()}-{seq:04d}",
                utterance=utt,
                positive=spec["pos"],
                negative=spec["neg"],
                corrections=spec["corr"],
                family=fam,
                pattern=pat,
                pair_group_id=gid,
                difficulty="hard",
                secondary=[],
                neg_subtype=spec["neg_sub"],
            )
            if commit(row, meta, neg_subtype=spec["neg_sub"]):
                group_ok.append(meta["id"])
            else:
                quotas[(fam, pat)] += 1  # refund
        if len(group_ok) < 2:
            # dissolve singleton group ids
            for mid in group_ok:
                for m in metas:
                    if m["id"] == mid:
                        m["pair_group_id"] = None
            if gid in pair_groups:
                del pair_groups[gid]
        else:
            # require at least two distinct golds in the group
            golds = []
            for mid in group_ok:
                for r in delta:
                    if r["id"] == mid:
                        golds.append(
                            json.dumps(
                                r["need_profile"]["semantic_constraints"],
                                sort_keys=True,
                            )
                        )
                        break
            if len(set(golds)) < 2:
                for mid in group_ok:
                    for m in metas:
                        if m["id"] == mid:
                            m["pair_group_id"] = None
                if gid in pair_groups:
                    del pair_groups[gid]
        if pair_i > 5000:
            break

    # --- Phase B: fill remaining quotas with natural singletons (no pair id) ---
    fill_i = 0
    guard = 0
    while sum(quotas.values()) > 0 and guard < 50000:
        guard += 1
        # pick first non-empty quota
        item = next(((f, p, n) for (f, p), n in quotas.items() if n > 0), None)
        if not item:
            break
        fam, pat, _ = item
        reject, want = PAIRS[fill_i % len(PAIRS)]
        if rng.random() < 0.5:
            reject, want = want, reject
        sr, sw = _surface(rng, reject, fill_i), _surface(rng, want, fill_i + 3)
        bud = BUDGETS[fill_i % len(BUDGETS)]
        fill_i += 1
        w2 = next(c for c, _ in ENTITIES if c not in {want, reject})
        sw2 = _surface(rng, w2, fill_i)

        spec: Optional[dict[str, Any]] = None
        if fam == "CORRECTION" and pat == "CORRECTION_X_NOT_Y":
            templates = [
                f"hayır {sr} değil {sw} bakıyorum",
                f"aslında {sr} değil {sw} arıyorum",
                f"düzelteyim: {sr} değil {sw}",
                f"yanlış söyledim {sr} değil {sw} olsun",
                f"özür dilerim, {sr} değil {sw} istiyorum",
            ]
            utt = templates[fill_i % len(templates)]
            spec = {"utt": utt, "pos": [want], "neg": [reject], "corr": [want], "neg_sub": "true_negative"}
        elif fam == "CORRECTION" and pat == "CORRECTION_RETRACTION":
            templates = [
                f"{sr} boşver, {sw} alacağız",
                f"fikrimi değiştirdim {sr} değil {sw}",
                f"{sr}i bıraktım {sw} arıyorum",
                f"önceki tercihim {sr}di ama şimdi {sw}",
            ]
            spec = {"utt": templates[fill_i % len(templates)], "pos": [want], "neg": [reject], "corr": [want], "neg_sub": "true_negative"}
        elif fam == "CORRECTION" and pat == "CORRECTION_PREVIOUS_TURN":
            templates = [
                f"az önce {sr} demiştim ama {sw} istiyorum",
                f"önceki mesajdaki {sr} yanlıştı, {sw} lazım",
                f"dün {sr} bakıyorduk bugün {sw} karar verdik",
                f"başta {sr} sandım, aslında {sw}",
            ]
            spec = {"utt": templates[fill_i % len(templates)], "pos": [want], "neg": [reject], "corr": [want], "neg_sub": "true_negative"}
        elif fam == "CORRECTION" and pat == "NEGATION_OF_NEGATION":
            templates = [
                f"{sw} istemiyorum demiyorum, bakıyorum",
                f"ben {sw} istemiyorum demedim ki",
                f"{sw} olmasın demedim",
            ]
            spec = {"utt": templates[fill_i % len(templates)], "pos": [want], "neg": [], "corr": [], "neg_sub": "negation_of_negation"}
        elif fam == "CORRECTION" and pat == "CORRECTION_MULTI_ENTITY":
            templates = [
                f"{sr} değil {sw} veya {sw2} olabilir",
                f"yanlış: {sr} değil, {sw} ya da {sw2} bakıyorum",
                f"{sr} olmasın; {sw} ve {sw2} düşünüyoruz",
            ]
            spec = {"utt": templates[fill_i % len(templates)], "pos": [want, w2], "neg": [reject], "corr": [want], "neg_sub": "true_negative"}
        elif fam == "CORRECTION" and pat == "BUDGET_PLUS_CORRECTION":
            templates = [
                f"{bud} bine {sr} değil {sw} bakıyorum",
                f"bütçe {bud} civarı, {sr} değil {sw}",
                f"max {bud}, {sr} olmasın {sw} istiyorum",
            ]
            spec = {
                "utt": templates[fill_i % len(templates)],
                "pos": [want], "neg": [reject], "corr": [want], "neg_sub": "true_negative",
                "budget": _budget("APPROXIMATE" if fill_i % 2 == 0 else "RANGE", bud),
            }
        elif fam == "NEGATION_HARD_NEGATIVE" and pat == "NEG_SIMPLE":
            templates = [
                f"{sr} olmasın {sw} lazım",
                f"{sr} istemem, {sw} bakıyorum",
                f"{sw} istiyorum {sr} değil",
                f"{sr} sarmıyor {sw} bakıyorum",
            ]
            spec = {"utt": templates[fill_i % len(templates)], "pos": [want], "neg": [reject], "corr": [], "neg_sub": "true_negative"}
        elif fam == "NEGATION_HARD_NEGATIVE" and pat == "MULTI_POS_SINGLE_NEG":
            templates = [
                f"{sw} veya {sw2} olabilir ama {sr} olmasın",
                f"{sw}/{sw2} bakıyorum, {sr} istemiyorum",
                f"{sr} hariç {sw} ya da {sw2}",
            ]
            spec = {"utt": templates[fill_i % len(templates)], "pos": [want, w2], "neg": [reject], "corr": [], "neg_sub": "true_negative"}
        elif fam == "NEGATION_HARD_NEGATIVE" and pat == "CORRECTION_X_NOT_Y":
            templates = [f"{sr} değil {sw}", f"yok {sr}, {sw} olsun", f"{sr} yerine {sw}"]
            spec = {"utt": templates[fill_i % len(templates)], "pos": [want], "neg": [reject], "corr": [want], "neg_sub": "true_negative"}
        elif fam == "NEGATION_HARD_NEGATIVE" and pat == "SOFT_PREFERENCE_NOT_NEGATIVE":
            templates = [
                f"{sr} kötü demiyorum ama {sw} tercih ederim",
                f"{sr} olmasa da olur, {sw} daha iyi",
                f"{sr} şart değil {sw} bakıyorum",
            ]
            spec = {"utt": templates[fill_i % len(templates)], "pos": [want], "neg": [], "corr": [], "neg_sub": "soft_preference"}
        elif fam == "NEGATION_HARD_NEGATIVE" and pat == "COMPARISON_NOT_NEGATIVE":
            templates = [
                f"{sw} mu {sr} mi emin değilim",
                f"{sw} ile {sr} karşılaştırıyorum",
                f"{sw} mi alayım {sr} mi karar veremedim",
            ]
            spec = {
                "utt": templates[fill_i % len(templates)],
                "pos": [want, reject], "neg": [], "corr": [], "neg_sub": "comparison_only",
                "clarify": True, "confidence": 0.5,
            }
        elif fam == "POSITIVE_MISS_EMPTY" and pat == "DIRECT_POSITIVE":
            templates = [f"{sw} arıyorum", f"{sw} istiyorum", f"{sw} lazım", f"bana bir {sw} öner"]
            spec = {"utt": templates[fill_i % len(templates)], "pos": [want], "neg": [], "corr": [], "neg_sub": None}
        elif fam == "POSITIVE_MISS_EMPTY" and pat == "COLLOQUIAL_POSITIVE":
            templates = [f"{sw} fln bakıyoruz", f"{sw} baya lazım oldu", f"bi {sw} alıcaz"]
            spec = {"utt": templates[fill_i % len(templates)], "pos": [want], "neg": [], "corr": [], "neg_sub": None}
        elif fam == "POSITIVE_MISS_EMPTY" and pat == "IMPLICIT_PURCHASE_INTENT":
            templates = [f"stüdyo işi için {sw}", f"ev için {sw} düşünüyoruz", f"okulda kullanmak üzere {sw}"]
            spec = {"utt": templates[fill_i % len(templates)], "pos": [want], "neg": [], "corr": [], "neg_sub": None}
        elif fam == "POSITIVE_MISS_EMPTY" and pat == "MULTI_POSITIVE":
            templates = [f"{sw} ve {sw2} bakıyorum", f"hem {sw} hem {sw2} lazım"]
            spec = {"utt": templates[fill_i % len(templates)], "pos": [want, w2], "neg": [], "corr": [], "neg_sub": None}
        elif fam == "POSITIVE_MISS_EMPTY" and pat == "POSITIVE_WITH_BUDGET":
            templates = [f"{sw} bakıyoruz, {bud} bin civarı", f"{bud} TL bandında {sw}", f"{sw} istiyorum {bud}i geçmesin"]
            spec = {
                "utt": templates[fill_i % len(templates)],
                "pos": [want], "neg": [], "corr": [], "neg_sub": None,
                "budget": _budget("APPROXIMATE" if fill_i % 2 else "RANGE", bud),
            }
        elif fam == "OVER_EXTRACTION_SUPPRESSION" and pat == "DIRECT_POSITIVE":
            templates = [f"sadece {sw}", f"yalnızca {sw} bakıyorum", f"tek ihtiyaç: {sw}", f"{sw} yeterli, başka önerme"]
            spec = {"utt": templates[fill_i % len(templates)], "pos": [want], "neg": [], "corr": [], "neg_sub": None}
        elif fam == "OVER_EXTRACTION_SUPPRESSION" and pat == "NEG_SIMPLE":
            templates = [f"{sr} istemiyorum sadece {sw}", f"sadece {sw}, {sr} olmasın"]
            spec = {"utt": templates[fill_i % len(templates)], "pos": [want], "neg": [reject], "corr": [], "neg_sub": "true_negative"}
        elif fam == "OVER_EXTRACTION_SUPPRESSION" and pat == "SOFT_PREFERENCE_NOT_NEGATIVE":
            templates = [f"{sw} istiyorum, {sr} şart değil", f"{sw} odaklıyım {sr} belki sonra"]
            spec = {"utt": templates[fill_i % len(templates)], "pos": [want], "neg": [], "corr": [], "neg_sub": "soft_preference"}
        elif fam == "OVER_EXTRACTION_SUPPRESSION" and pat == "AMBIGUOUS_EXPECT_EMPTY":
            templates = [
                "ne alsam bilmiyorum henüz",
                "bir şey lazım ama ne olduğu belirsiz",
                "öneri isterim ürün söylemeden",
                "kararsızım hiçbir şey net değil",
            ]
            spec = {
                "utt": templates[fill_i % len(templates)],
                "pos": [], "neg": [], "corr": [], "neg_sub": None,
                "intent": "CLARIFICATION_RESPONSE", "clarify": True, "confidence": 0.4,
            }
        elif fam == "CONFLICT_PREVENTION" and pat == "CORRECTION_X_NOT_Y":
            templates = [
                f"{sr} değil {sw} net olarak",
                f"yanlış anlaşılmasın {sr} değil {sw}",
                f"son karar: {sr} değil {sw} istiyorum",
            ]
            if {want, reject} == {"e-bike", "bisiklet"}:
                templates = [
                    "elektrikli bisiklet istiyorum pedallı bisiklet olmasın",
                    "e-bike bakıyorum klasik bisiklet istemiyorum",
                    "pedalsız değil elektrikli bisiklet istiyorum",
                ]
                # last template awkward — keep first two styles
                templates = templates[:2] + ["elektrikli bisiklet lazım, bisiklet değil"]
                want, reject = "e-bike", "bisiklet"
            utt = templates[fill_i % len(templates)]
            spec = {
                "utt": utt,
                "pos": [want],
                "neg": [reject],
                "corr": [want],
                "neg_sub": "true_negative",
            }
        elif fam == "CONFLICT_PREVENTION" and pat == "NEG_SIMPLE":
            templates = [
                f"{sw} alacağım {sr} almayacağım",
                f"{sw} istiyorum ve {sr} istemiyorum",
                f"tercihim {sw}; {sr} listede olmasın",
            ]
            spec = {"utt": templates[fill_i % len(templates)], "pos": [want], "neg": [reject], "corr": [], "neg_sub": "true_negative"}
        elif fam == "AMBIGUOUS_EXPECT_EMPTY":
            topics = [
                ("hava durumu", "OUT_OF_SCOPE"),
                ("trafik", "OUT_OF_SCOPE"),
                ("futbol skoru", "OUT_OF_SCOPE"),
                ("banka şubesi", "OUT_OF_SCOPE"),
                ("kredi kartı", "OUT_OF_SCOPE"),
                ("hesap açılışı", "OUT_OF_SCOPE"),
                ("döviz", "OUT_OF_SCOPE"),
                ("ödev yardımı", "OUT_OF_SCOPE"),
                ("yemek tarifi", "OUT_OF_SCOPE"),
                ("taksit seçenekleri", "BUDGET_INQUIRY"),
                ("aylık ödeme", "BUDGET_INQUIRY"),
                ("bütçe planı", "BUDGET_INQUIRY"),
                ("kampanya takvimi", "OTHER"),
                ("iade politikası", "OTHER"),
                ("kargo süresi genel", "OTHER"),
                ("kararsızlık", "CLARIFICATION_RESPONSE"),
                ("ihtiyaç analizi", "CLARIFICATION_RESPONSE"),
                ("isim vermeden yönlendirme", "CLARIFICATION_RESPONSE"),
                ("iki seçenek karşılaştırması isimsiz", "COMPARE_OPTIONS"),
                ("genel bakış almadan önce", "OTHER"),
            ]
            openers = [
                "merhaba",
                "selam",
                "günaydın",
                "iyi akşamlar",
                "bir sorum var",
                "acele yok ama",
                "müsaadenizle",
                "kısaca sorayım",
                "bilgi almak istiyorum",
                "yardım eder misiniz",
            ]
            closers = [
                "ürün adı yok",
                "model söylemiyorum",
                "henüz seçmedim",
                "sadece genel",
                "karar vermeden",
                "isim bilmiyorum",
                "netleşmeden",
                "şimdilik belirsiz",
            ]
            topic, intent = topics[fill_i % len(topics)]
            opener = openers[(fill_i // 3) % len(openers)]
            closer = closers[(fill_i // 5) % len(closers)]
            bodies = [
                f"{opener}, {topic} hakkında {closer}",
                f"{topic} için soru: {closer}",
                f"{opener}; {closer}, {topic} soruyorum",
                f"{topic} bilgisini {closer} istiyorum",
                f"{opener} {topic} anlatır mısın, {closer}",
            ]
            utt = bodies[fill_i % len(bodies)]
            spec = {
                "utt": utt,
                "pos": [], "neg": [], "corr": [], "neg_sub": None,
                "intent": intent,
                "clarify": intent in {"CLARIFICATION_RESPONSE", "COMPARE_OPTIONS", "OTHER", "BUDGET_INQUIRY"},
                "confidence": 0.85 if intent == "OUT_OF_SCOPE" else 0.45,
                "no_product_frame": True,
            }
        else:
            continue

        if not take(fam, pat):
            continue
        seq += 1
        utt = spec["utt"] if spec.get("no_product_frame") else _frame(rng, spec["utt"], seq + fill_i)
        row, meta = make_row(
            case_id=f"p17v4s-fill-{fam[:3].lower()}-{pat[:10].lower()}-{seq:04d}",
            utterance=utt,
            positive=spec["pos"],
            negative=spec["neg"],
            corrections=spec["corr"],
            family=fam,
            pattern=pat,
            pair_group_id=None,
            difficulty="medium",
            secondary=[],
            neg_subtype=spec.get("neg_sub"),
            intent=spec.get("intent", "PRODUCT_PURCHASE"),
            budget=spec.get("budget"),
            clarify=bool(spec.get("clarify")),
            confidence=float(spec.get("confidence") or 0.9),
        )
        if not commit(row, meta, neg_subtype=spec.get("neg_sub")):
            quotas[(fam, pat)] += 1

    # dissolve any remaining singleton pair groups
    singleton = [g for g, ids in pair_groups.items() if len(ids) < 2]
    for g in singleton:
        for m in metas:
            if m.get("pair_group_id") == g:
                m["pair_group_id"] = None
        del pair_groups[g]

    # exact family/pattern counts check — refill impossible leftovers by force-generating uniques
    got = Counter((m["primary_family"], m["primary_pattern"]) for m in metas)
    shortfalls = []
    for fam, pats in PATTERN_TARGETS.items():
        for p, n in pats.items():
            have = got.get((fam, p), 0)
            if have != n:
                shortfalls.append(((fam, p), n - have))

    # If short/over, raise with detail — caller can fail validation
    stats = {
        "neg_stats": dict(neg_stats),
        "pair_groups": {k: v for k, v in pair_groups.items()},
        "quotas_remaining": {f"{f}/{p}": n for (f, p), n in quotas.items() if n},
        "shortfalls": [(f"{a}/{b}", d) for (a, b), d in shortfalls],
        "delta_count": len(delta),
    }
    return delta, metas, stats


def load_eval_utterances() -> list[str]:
    out = list(BANNED_NEAR)
    paths = [
        _ROOT / "evaluation/datasets/development/tr-category-dev.v4.jsonl",
        _ROOT / "evaluation/datasets/validation/tr-category-validation.v4.jsonl",
        _ROOT / "evaluation/datasets/golden/tr-category-holdout.v1.jsonl",
        _ROOT / "evaluation/datasets/golden/tr-category-validation.v1.jsonl",
        _ROOT / "artifacts/p17/v3/residual_raw.jsonl",
    ]
    for p in paths:
        if not p.is_file():
            continue
        for line in p.open(encoding="utf-8"):
            if not line.strip():
                continue
            o = json.loads(line)
            utt = o.get("utterance") or o.get("message")
            if utt:
                out.append(str(utt))
    return out


def validate_all(
    base: list[dict[str, Any]],
    delta: list[dict[str, Any]],
    metas: list[dict[str, Any]],
    base_report: dict[str, Any],
    delta_stats: dict[str, Any],
    out_dir: Path,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = out_dir / "data"
    data.mkdir(parents=True, exist_ok=True)

    train = base + delta
    eval_utts = load_eval_utterances()
    eval_idx = EvalIndex(eval_utts)

    # write data first
    def dump(path: Path, rows: list[dict[str, Any]]) -> None:
        with path.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    dump(data / "need_profile_sft.v4.clean.base.jsonl", base)
    dump(data / "need_profile_sft.v4.clean.delta.jsonl", delta)
    dump(data / "need_profile_sft.v4.clean.train.jsonl", train)
    with (data / "v4_clean_row_metadata.jsonl").open("w", encoding="utf-8") as fh:
        for m in metas:
            fh.write(json.dumps(m, ensure_ascii=False) + "\n")

    blockers: list[dict[str, Any]] = []
    # distribution
    fam = Counter(m["primary_family"] for m in metas)
    pat = Counter((m["primary_family"], m["primary_pattern"]) for m in metas)
    fam_ok = dict(fam) == FAMILY_TARGETS
    pat_ok = all(pat.get((f, p), 0) == n for f, ps in PATTERN_TARGETS.items() for p, n in ps.items())
    if not fam_ok or not pat_ok or len(delta) != 1200:
        blockers.append({
            "type": "distribution",
            "delta": len(delta),
            "fam": dict(fam),
            "pat_ok": pat_ok,
            "shortfalls": delta_stats.get("shortfalls"),
            "remaining": delta_stats.get("quotas_remaining"),
        })

    # semantic over full train
    schema_fail = 0
    conflict = 0
    forbidden = 0
    corr_dir_err = 0
    corr_empty = 0
    artificial = 0
    cat_hint = 0
    draft = 0
    eval_src = 0
    for r in train:
        if r.get("annotation_status") == "DRAFT":
            draft += 1
        path = str((r.get("source_ref") or {}).get("path") or "")
        if "/evaluation/" in path or "/validation/" in path:
            eval_src += 1
        if "__up" in str(r.get("id") or ""):
            eval_src += 1
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
        if any(str(p.get("concept", "")).startswith("category_hint:") for p in (r["need_profile"].get("preferences") or [])):
            cat_hint += 1
        utt = r["utterance"]
        if ARTIFICIAL_RE.search(utt) or ARTIFICIAL_RE.search(str(r["need_profile"].get("need_description") or "")):
            artificial += 1
        if looks_like_correction_utt(utt):
            if not (sc.get("corrections") or []):
                corr_empty += 1
            # direction check for X değil Y
            m = re.search(
                r"([\wçğıöşü]+(?:\s+[\wçğıöşü]+)?)\s+değil\s+([\wçğıöşü]+(?:\s+[\wçğıöşü]+)?)",
                utt.casefold(),
            )
            if m and pos and neg:
                left, right = m.group(1).strip(), m.group(2).strip()
                # if a positive concept appears only on left and a negative on right → inverted
                if any(p in left and p not in right for p in pos) and any(
                    n in right and n not in left for n in neg
                ):
                    corr_dir_err += 1

    # leakage full train vs eval
    exact_eval = 0
    near_eval = 0
    for r in train:
        hit = eval_idx.hit(r["utterance"])
        if not hit:
            continue
        if hit.startswith("exact:"):
            exact_eval += 1
        else:
            near_eval += 1

    # duplicates
    raw_counts = Counter(r["utterance"] for r in train)
    norm_counts = Counter(normalize_utt(r["utterance"]) for r in train)
    raw_dups = sum(c - 1 for c in raw_counts.values() if c > 1)
    norm_dups = sum(c - 1 for c in norm_counts.values() if c > 1)

    # pairs
    groups: dict[str, list[str]] = defaultdict(list)
    for m in metas:
        if m.get("pair_group_id"):
            groups[m["pair_group_id"]].append(m["id"])
    singleton = sum(1 for ids in groups.values() if len(ids) < 2)
    invalid_pairs = 0
    id_to_row = {r["id"]: r for r in delta}
    for gid, ids in groups.items():
        if len(ids) < 2:
            continue
        golds = [
            json.dumps(id_to_row[i]["need_profile"]["semantic_constraints"], sort_keys=True)
            for i in ids
            if i in id_to_row
        ]
        if len(set(golds)) < 2:
            invalid_pairs += 1

    for name, val in [
        ("schema_fail", schema_fail),
        ("draft", draft),
        ("eval_src", eval_src),
        ("exact_eval", exact_eval),
        ("near_eval", near_eval),
        ("corr_dir_err", corr_dir_err),
        ("corr_empty", corr_empty),
        ("conflict", conflict),
        ("forbidden", forbidden),
        ("artificial", artificial),
        ("cat_hint", cat_hint),
        ("singleton_pairs", singleton),
        ("invalid_pairs", invalid_pairs),
    ]:
        if val:
            blockers.append({"type": name, "count": val})
    # duplicates are reported separately (not automatic REJECT)

    decision = (
        "V4_SANITIZED_DATASET_READY_FOR_SFT" if not blockers else "V4_SANITIZED_DATASET_REJECT"
    )

    canonical = {
        "canonical_language": "tr",
        "canonical_concept_examples": ["telefon", "laptop", "tablet", "e-bike", "kulaklık"],
        "alias_normalization_policy": (
            "Aliases are noun phrases only (no full clauses / embedded negation). "
            "semantic_constraints.concept uses Turkish canonical noun; aliases may appear in utterance surface."
        ),
        "preferences_policy": (
            "preferences mirror positive concepts only; category_hint:* forbidden in sanitized gold"
        ),
        "semantic_constraints_policy": (
            "positive=EXPLICIT wanted concepts; negative=EXPLICIT_NEGATION rejected; "
            "corrections=USER_CORRECTION replacement concept; positive∩negative must be empty"
        ),
        "category_hint_policy": (
            "REMOVED from sanitized train. Prompt forbids category IDs/fixture keys; "
            "category_hint:MOBILE_PHONE-style prefs are out of contract for this dataset."
        ),
        "correction_contract": {
            "utterance": "X değil Y istiyorum",
            "positive": "Y",
            "negative": "X",
            "corrections": "Y + USER_CORRECTION",
        },
    }

    (out_dir / "base_exclusion_report.json").write_text(
        json.dumps(base_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out_dir / "correction_audit.json").write_text(
        json.dumps({
            "repaired_in_base": base_report.get("repaired_correction_rows"),
            "dropped_in_base": base_report.get("dropped_correction_rows"),
            "corr_direction_errors_final_train": corr_dir_err,
            "corr_empty_final_train": corr_empty,
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "canonical_contract.json").write_text(
        json.dumps(canonical, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out_dir / "merged_leakage_report.json").write_text(
        json.dumps({
            "method": {"normalize": "NFKC+casefold", "jaccard": 0.72},
            "eval_source_rows": eval_src,
            "draft_rows": draft,
            "exact_eval_leakage": exact_eval,
            "near_eval_leakage": near_eval,
            "upsample_rows": sum(1 for r in train if "__up" in str(r.get("id") or "")),
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "duplicate_report.json").write_text(
        json.dumps({
            "raw_duplicate_extra_rows": raw_dups,
            "normalized_duplicate_extra_rows": norm_dups,
            "unique_raw": len(raw_counts),
            "unique_normalized": len(norm_counts),
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "minimal_pair_report.json").write_text(
        json.dumps({
            "minimal_pair_group_count": len(groups),
            "minimal_pair_row_count": sum(len(v) for v in groups.values()),
            "singleton_pair_group_count": singleton,
            "invalid_pair_group_count": invalid_pairs,
            "size_histogram": dict(Counter(len(v) for v in groups.values())),
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "semantic_validation.json").write_text(
        json.dumps({
            "schema_fail": schema_fail,
            "correction_direction_errors": corr_dir_err,
            "correction_rows_with_empty_corrections": corr_empty,
            "positive_negative_conflicts": conflict,
            "forbidden": forbidden,
            "artificial_markers": artificial,
            "category_hint_violations": cat_hint,
            "prompt_contract_note": "category_hint removed; concepts are Turkish surface nouns",
        }, indent=2) + "\n",
        encoding="utf-8",
    )

    validation = {
        "experiment_id": EXPERIMENT,
        "clean_base_rows": len(base),
        "clean_delta_rows": len(delta),
        "final_train_rows": len(train),
        "family_pass": fam_ok,
        "pattern_pass": pat_ok,
        "blockers": blockers,
        "decision": decision,
        "campaign_gate": "CLOSED",
        "v4_training": "NOT_STARTED",
        "quant_attribution": "NOT_TESTED",
        "metrics": {
            "schema_fail": schema_fail,
            "draft": draft,
            "eval_src": eval_src,
            "exact_eval": exact_eval,
            "near_eval": near_eval,
            "corr_dir_err": corr_dir_err,
            "corr_empty": corr_empty,
            "conflict": conflict,
            "forbidden": forbidden,
            "artificial": artificial,
            "singleton_pairs": singleton,
            "invalid_pairs": invalid_pairs,
            "norm_dups": norm_dups,
        },
    }
    (out_dir / "p17_v4_sanitize_validation.json").write_text(
        json.dumps(validation, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    report = f"""# P17-V4-DATASET-SANITIZE-001 Report

**Created:** `{_utc()}`  
**Decision:** `{decision}`  
**Campaign Gate:** CLOSED · **V4 training:** NOT STARTED · **Quant:** NOT TESTED

## Counts

| Item | Value |
|---|---|
| Clean base | {len(base)} |
| Clean delta | {len(delta)} / 1200 |
| Final train | {len(train)} |
| Removed DRAFT | {base_report.get('removed_draft')} |
| Removed eval-source | {base_report.get('removed_eval_source')} |
| Removed near-eval (base) | {base_report.get('removed_near_eval_from_base')} |
| Removed norm-dups (base) | {base_report.get('removed_normalized_dups_from_base')} |
| Repaired corrections | {base_report.get('repaired_correction_rows')} |
| Dropped corrections | {base_report.get('dropped_correction_rows')} |
| Family/Pattern | {'PASS' if fam_ok and pat_ok else 'FAIL'} |
| Blockers | {len(blockers)} |

## Semantic / leakage (full train)

```json
{json.dumps(validation['metrics'], indent=2)}
```

## Canonical contract

See `canonical_contract.json` — Turkish surface concepts; `category_hint:*` removed.

## Final

```text
P17-V4-DATASET-SANITIZE-001 = {'COMPLETE' if decision.endswith('READY_FOR_SFT') else 'INCOMPLETE'}
Clean base rows             = {len(base)}
Clean delta rows            = {len(delta)} / 1200
Final train rows            = {len(train)}
Removed eval-source rows    = {base_report.get('removed_eval_source')}
Removed DRAFT rows          = {base_report.get('removed_draft')}
Repaired correction rows    = {base_report.get('repaired_correction_rows')}
Dropped correction rows     = {base_report.get('dropped_correction_rows')}
Artificial markers          = {artificial}
Eval leakage                = {exact_eval + near_eval}
Semantic blockers           = {len(blockers)}
Dataset decision            = {decision}
V4 training                 = NOT STARTED
Quant attribution           = NOT TESTED
Campaign Gate               = CLOSED
```
"""
    (out_dir / "p17_v4_sanitize_report.md").write_text(report, encoding="utf-8")
    return validation


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=19)
    ap.add_argument("--v3", type=Path, default=_ROOT / "training/exports/need_profile_sft.v3.jsonl")
    ap.add_argument("--out-dir", type=Path, default=_ROOT / "artifacts" / "p17" / "v4-sanitized")
    args = ap.parse_args()

    assert sum(FAMILY_TARGETS.values()) == 1200
    print(f"[{_utc()}] cleaning base…", flush=True)
    base, base_report = clean_base(args.v3)
    eval_utts = load_eval_utterances()
    eval_idx = EvalIndex(eval_utts)
    # drop base rows near eval
    filtered_base = []
    near_dropped = 0
    for r in base:
        if eval_idx.hit(r["utterance"]):
            near_dropped += 1
            continue
        filtered_base.append(r)
    base_report["removed_near_eval_from_base"] = near_dropped
    # drop normalized duplicates in base (keep first); uniqueness via id only is not enough for train surface
    deduped: list[dict[str, Any]] = []
    seen_norm: set[str] = set()
    norm_dup_dropped = 0
    for r in filtered_base:
        nu = normalize_utt(r["utterance"])
        if not nu or nu in seen_norm:
            norm_dup_dropped += 1
            continue
        seen_norm.add(nu)
        deduped.append(r)
    base_report["removed_normalized_dups_from_base"] = norm_dup_dropped
    base_report["kept_rows"] = len(deduped)
    base = deduped
    print(
        f"[{_utc()}] base kept={len(base)} near_eval_dropped={near_dropped} "
        f"norm_dup_dropped={norm_dup_dropped} excl={base_report['exclusions']}",
        flush=True,
    )

    print(f"[{_utc()}] generating clean delta…", flush=True)
    base_norms = {normalize_utt(r["utterance"]) for r in base}
    delta = metas = stats = None
    for seed in range(args.seed, args.seed + 60):
        d, m, s = generate_clean_delta(
            seed=seed, eval_utts=eval_utts, base_norms=base_norms, eval_index=eval_idx
        )
        got = Counter((x["primary_family"], x["primary_pattern"]) for x in m)
        ok = len(d) == 1200 and all(
            got.get((f, p), 0) == n for f, ps in PATTERN_TARGETS.items() for p, n in ps.items()
        )
        print(
            f"  seed={seed} delta={len(d)} shortfalls={s.get('shortfalls')[:4]} "
            f"remaining={list(s.get('quotas_remaining', {}).items())[:4]}",
            flush=True,
        )
        if ok:
            delta, metas, stats = d, m, s
            break
    if delta is None:
        delta, metas, stats = d, m, s

    print(f"[{_utc()}] validating…", flush=True)
    validation = validate_all(base, delta, metas, base_report, stats, args.out_dir)
    print(json.dumps(validation, indent=2, ensure_ascii=False))
    if validation["decision"] != "V4_SANITIZED_DATASET_READY_FOR_SFT":
        raise SystemExit(2)


if __name__ == "__main__":
    main()

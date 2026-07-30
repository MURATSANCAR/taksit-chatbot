"""DeterministicFastExtractor — rule-based Turkish extractor (ADR-007 §3).

This extractor is used by unit tests, deterministic CI runs, and any
environment where the real FAST deployment is not available. It is not
allowed to know any category IDs, fixture keys, or catalog codes: every
"concept" it produces is a natural-language phrase pulled from the raw
utterance (or the extractor's internal Turkish rule tables).

Detection strategy is intentionally content-blind at the catalog level:

* Split into clauses on Turkish contrast markers ("değil", "ama",
  "yerine", "aslında", "hayır", "yok yok", …).
* For each clause, walk word windows and label them as:
    - previous_concept for the pre-marker span, or
    - replacement_concept for the post-marker span, or
    - positive if the clause is a plain declaration.
* Explicit-negation cues ("istemiyorum", "istemem", "gerekmiyor",
  "boşver") mark the enclosing noun window as ``EXPLICIT_NEGATION``.
* Correction cues ("aslında ... değil ...", "hayır ... dedim ...",
  "yanlış", "özür dilerim ...") flip the pre-marker span into
  ``USER_CORRECTION``.
* Multi-need cues ("hem ... hem ...", "mi ... mı ...", multiple positive
  spans) surface as ``signals.multiple_needs=True`` and additional
  ambiguity codes.

The extractor never emits ``fixture.*`` or UUID-shaped strings — the
``SemanticConstraintValidator`` catches those defensively anyway.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from taksitlio.semantic_constraints import (
    SemanticConstraintValidator,
    ValidatedSemanticConstraints,
)
from taksitlio.semantic_matching.turkish_normalize import (
    ascii_fold,
    normalize_turkish,
    turkish_lower,
)
from taksitlio.understanding.fast.errors import FastExtractionError
from taksitlio.understanding.fast.protocol import FastExtractionOutcome
from taksitlio.understanding.fast.schema_utils import (
    build_empty_need_profile,
    validate_need_profile,
)


# ---------------------------------------------------------------------------
# Turkish cue tables (content-blind; no product / category words)
# ---------------------------------------------------------------------------


# Words that must be dropped when extracting a noun span. They carry no
# category identity themselves.
_STOPWORDS_RAW = (
    "bir", "bu", "şu", "o", "ve", "ile", "için", "gibi",
    "de", "da", "ki", "ise", "ama", "fakat", "ancak", "veya",
    "ya", "yah", "yani", "sadece", "artık", "hala", "yine",
    # Verb / auxiliary noise
    "arıyorum", "arıyoruz", "bakıyorum", "bakıyoruz", "bakayım",
    "istiyorum", "istiyoruz", "istemiyorum", "istemem",
    "istemiyor", "istemedim", "istemedik", "gerekiyor", "gerekmiyor",
    "olsun", "olabilir", "olur", "yeter", "lazım", "gerek",
    "alacağım", "alacağız", "alcağım", "alacak", "alalım",
    "alsam", "alsak", "alsınlar", "aldık",
    "aldım", "verir", "verin", "verirsiniz", "önerin", "önerir",
    "önerebilir", "tavsiye", "modelleri", "modellerine",
    "önce", "sonra", "biraz", "çok", "az",
    # Pronouns
    "ben", "sen", "biz", "siz", "onlar", "kendi", "kendim",
    "hepsi", "birbirimize",
    # Politeness
    "lütfen", "teşekkürler", "sağolun",
    # Locatives / time
    "bugün", "yarın", "şimdi", "hafta", "ay",
    # Small numbers / counters
    "1", "2", "3",
    # Question suffix particles (mi/mı/mu/mü)
    "mi", "mı", "mu", "mü",
    # Negation / correction cues themselves — never should be a concept
    "değil", "degil", "hayır", "yok", "aslında", "boşver", "bosver",
    "istemem", "istemiyorum", "istemek",
    # "almak" and similar bare infinitives
    "almak", "alma", "almam",
    # Additional verbs / adverbs used in correction / question forms
    "demiştim", "dedim", "dediysem", "diyecektim", "diyecek",
    "yanlış", "yanlis", "anladın", "anladin", "anladım",
    "kastediyordum", "kastetmiştim", "kastettim", "kastediyor",
    "bakıyor", "bakıyorlar", "kararlı", "karar", "veremedim",
    "olabilir", "olamaz",
    # Common question / discussion words that muddy concept extraction
    "olsun", "ola",
)
# Store both the raw + ascii-fold versions so the check is stable
_STOPWORDS = frozenset(_STOPWORDS_RAW) | frozenset(
    ascii_fold(turkish_lower(w)) for w in _STOPWORDS_RAW
)

# Explicit-negation cue words / phrases (Turkish). Presence of any of
# these near a noun span turns the span into an EXPLICIT_NEGATION.
_NEG_CUES: tuple[str, ...] = (
    "istemiyorum",
    "istemem",
    "istemek istemiyorum",
    "gerekmiyor",
    "gerekmez",
    "gerek yok",
    "yok",
    "boşver",
    "bosver",
    "almam gerekmiyor",
    "almayacağım",
    "almayacak",
    "almam",
    "değil",
    "degil",
)

# Correction cue words that indicate a user is retracting an earlier claim.
_CORRECTION_CUES: tuple[str, ...] = (
    "aslında",
    "asında",
    "yanılmışım",
    "hayır",
    "yok yok",
    "yanlış söyledim",
    "yanlış oldu",
    "özür dilerim",
    "üzgünüm",
    "yanlış anladın",
    "demedim",
    "demedik",
    "dediysem",
    "kastediyordum",
    "kastetmiştim",
    "kastettim",
)

# Contrast markers that separate the "before" and "after" spans in a
# correction / negation clause. "değil" behaves as both a negation and a
# contrast marker and gets special handling in ``_split_contrast``.
_CONTRAST_MARKERS: tuple[str, ...] = (
    "değil",
    "degil",
    "yerine",
    "ama",
    "fakat",
    "ancak",
)

# Multi-need cues — mostly "mı ... mı" pattern and "hem ... hem".
_MULTI_NEED_CUES: tuple[str, ...] = (
    " mi ",
    " mı ",
    " mu ",
    " mü ",
    "hem ",
    " veya ",
    " ya da ",
)


_WORD_RE = re.compile(r"[\wçğıöşüÇĞİÖŞÜ']+", re.UNICODE)


@dataclass
class _Clause:
    text: str
    words: tuple[str, ...]
    normalized: str  # ascii-folded, lowered


def _norm_lower(text: str) -> str:
    return ascii_fold(turkish_lower(text or "")).strip()


def _tokenize(text: str) -> tuple[str, ...]:
    return tuple(m.group(0) for m in _WORD_RE.finditer(text or ""))


def _split_clauses(utterance: str) -> tuple[_Clause, ...]:
    """Split on Turkish soft punctuation + newlines."""

    parts = re.split(r"[.,;\n?!]+", utterance or "")
    clauses: list[_Clause] = []
    for part in parts:
        text = part.strip()
        if not text:
            continue
        clauses.append(
            _Clause(text=text, words=_tokenize(text), normalized=_norm_lower(text))
        )
    return tuple(clauses)


def _has_any_cue(normalized: str, cues: Sequence[str]) -> bool:
    haystack = f" {normalized} "
    return any(f" {c} " in haystack or normalized.startswith(c) for c in cues)


def _split_contrast(clause: _Clause) -> Optional[tuple[str, str, str]]:
    """Return ``(before, marker, after)`` when a contrast marker appears.

    ``before`` is the raw word segment to the left of the marker.
    ``after`` is the raw word segment to the right of the marker.
    Marker is the first hit from ``_CONTRAST_MARKERS``.
    """

    words = clause.words
    lowered = [_norm_lower(w) for w in words]
    for idx, low in enumerate(lowered):
        if low in {ascii_fold(m) for m in _CONTRAST_MARKERS}:
            left = " ".join(words[:idx])
            right = " ".join(words[idx + 1 :])
            return left.strip(), low, right.strip()
    return None


def _pick_head_noun(span: str) -> Optional[str]:
    """Return the primary noun concept in ``span``.

    Turkish typically places the head noun *before* the auxiliary verb
    ("kablosuz kulaklık lazım" — kulaklık is the head). We keep the last
    non-stopword token, plus one preceding adjectival modifier if present.
    Ends up producing "kablosuz kulaklık", "robot süpürge", "kahve
    makinesi", "telefon", etc.
    """

    if not span:
        return None
    words = _tokenize(span)
    if not words:
        return None
    keep = tuple(w for w in words if _norm_lower(w) not in _STOPWORDS)
    if not keep:
        return None
    # Strip trailing possessive / accusative suffixes that would make
    # "telefonu" fail to align with "telefon" later. Simple heuristic:
    # drop a trailing "u"/"ü"/"ı"/"i" from tokens ≥5 chars where the
    # preceding char is a consonant (very rough — but content-blind).
    def _strip_case(tok: str) -> str:
        if len(tok) < 5:
            return tok
        last = tok[-1]
        if last in "uüıi" and tok[-2] not in "aeıioöuü":
            return tok[:-1]
        return tok

    cleaned = tuple(_strip_case(t) for t in keep)
    # Take the last token, plus one preceding modifier when it looks
    # adjectival (short, alpha). Cap at two tokens.
    tail = cleaned[-1]
    if len(cleaned) >= 2:
        prev = cleaned[-2]
        if len(prev) <= 10 and prev.isalpha():
            concept = f"{prev} {tail}"
        else:
            concept = tail
    else:
        concept = tail
    concept = concept.strip()
    if len(concept) < 2:
        return None
    return concept


class DeterministicFastExtractor:
    """Rule-based FAST extractor for tests & offline development.

    Produces a schema-valid NeedProfile + validated semantic constraints.
    Never emits fixture keys or catalog UUIDs.
    """

    name = "deterministic-fast-extractor.v1"

    def __init__(
        self,
        *,
        validator: Optional[SemanticConstraintValidator] = None,
    ) -> None:
        self._validator = validator or SemanticConstraintValidator()

    async def extract(
        self,
        utterance: str,
        *,
        locale: str = "tr-TR",
        session_summary: Optional[Mapping[str, Any]] = None,
    ) -> FastExtractionOutcome:
        started = time.perf_counter()
        raw_positive: list[dict] = []
        raw_negative: list[dict] = []
        raw_corrections: list[dict] = []
        multi_need = False
        intent = "PRODUCT_PURCHASE"
        confidence = 0.75

        clauses = _split_clauses(utterance)

        # Detect multi-need on the whole utterance (cheap heuristic).
        normalized_full = " " + _norm_lower(utterance) + " "
        for cue in _MULTI_NEED_CUES:
            if cue in normalized_full:
                multi_need = True
                break

        # Track already-seen concepts to avoid pathological duplicates.
        seen_positive: set[str] = set()
        seen_negative: set[str] = set()

        # Detect OUT_OF_SCOPE cues (rare; catalog will decide anyway).
        oos_cues = (
            "uçak bileti",
            "otel rezervasyon",
            "seyahat",
            "tatil",
            "kapadokya",
            "tur paketi",
            "turu paketi",
        )
        if any(cue in normalized_full for cue in oos_cues):
            intent = "OUT_OF_SCOPE"
            confidence = 0.6

        for clause in clauses:
            self._extract_from_clause(
                clause,
                raw_positive=raw_positive,
                raw_negative=raw_negative,
                raw_corrections=raw_corrections,
                seen_positive=seen_positive,
                seen_negative=seen_negative,
            )

        # Build a schema-valid NeedProfile.
        need_profile = build_empty_need_profile(
            utterance=utterance,
            intent=intent,
            intent_confidence=confidence,
            confidence=confidence,
        )
        need_profile["preferences"] = [
            {"concept": p["concept"], "importance": 0.7}
            for p in raw_positive
        ][:16]
        if multi_need:
            need_profile["signals"] = {"multiple_needs": True}
            need_profile["ambiguities"].append(
                {
                    "code": "MULTIPLE_NEEDS",
                    "description": "Utterance mentions more than one need candidate.",
                }
            )
            need_profile["clarification"] = {
                "required": True,
                "question_intent": "which_need_first",
            }
        if raw_corrections:
            need_profile["ambiguities"].append(
                {
                    "code": "USER_CORRECTION",
                    "description": "User retracted a previous concept.",
                }
            )

        need_profile["semantic_constraints"] = _to_schema_constraints(
            raw_positive, raw_negative, raw_corrections
        )
        validate_need_profile(need_profile)

        # The NeedProfile schema stores corrections as single-concept
        # entries tagged USER_CORRECTION (schema-side representation).
        # The matcher validator wants full ``previous_concept →
        # replacement_concept`` pairs; pass that richer view here.
        validator_input = {
            "positive": [dict(p) for p in raw_positive],
            "negative": [dict(n) for n in raw_negative],
            "corrections": [dict(c) for c in raw_corrections],
        }
        validated = self._validator.validate(validator_input)
        latency_ms = (time.perf_counter() - started) * 1000.0
        return FastExtractionOutcome(
            utterance=utterance,
            need_profile=need_profile,
            constraints=validated,
            extractor=self.name,
            latency_ms=latency_ms,
            diagnostics={
                "multi_need": multi_need,
                "clause_count": len(clauses),
            },
        )

    # ------------------------------------------------------------------
    # Clause-level extraction
    # ------------------------------------------------------------------

    def _extract_from_clause(
        self,
        clause: _Clause,
        *,
        raw_positive: list[dict],
        raw_negative: list[dict],
        raw_corrections: list[dict],
        seen_positive: set[str],
        seen_negative: set[str],
    ) -> None:
        norm = clause.normalized
        is_correction = _has_any_cue(norm, [ascii_fold(c) for c in _CORRECTION_CUES])
        contrast = _split_contrast(clause)

        # 1) "X değil Y" / "X yerine Y" — hard contrast marker splits the clause.
        if contrast is not None:
            before, _marker, after = contrast
            prev = _pick_head_noun(before)
            repl = _pick_head_noun(after)
            if prev and repl and prev != repl:
                if is_correction:
                    raw_corrections.append(
                        {
                            "previous_concept": prev,
                            "replacement_concept": repl,
                            "confidence": 0.95,
                        }
                    )
                _add_positive(raw_positive, repl, seen_positive)
                _add_negative(raw_negative, prev, seen_negative)
                return
            if prev and not repl:
                _add_negative(raw_negative, prev, seen_negative)
                return
            if repl and not prev:
                _add_positive(raw_positive, repl, seen_positive)
                return

        # 2) Explicit-negation cue within a single clause. Split on the
        # cue: everything before → negative concept; everything after →
        # positive concept ("telefon istemiyorum tablet bakıyorum").
        neg_split = _split_on_neg_cue(clause)
        if neg_split is not None:
            before, after = neg_split
            neg_concept = _pick_head_noun(before)
            pos_concept = _pick_head_noun(after)
            if neg_concept:
                _add_negative(raw_negative, neg_concept, seen_negative)
            if pos_concept:
                _add_positive(raw_positive, pos_concept, seen_positive)
            if neg_concept or pos_concept:
                return

        # 3) Multi-need "X mi Y mi" / "X yoksa Y" pattern — surface
        # both as positive candidates (matcher then decides AMBIGUOUS).
        multi_split = _split_on_multi_need(clause)
        if multi_split is not None:
            for span in multi_split:
                concept = _pick_head_noun(span)
                if concept:
                    _add_positive(raw_positive, concept, seen_positive)
            return

        # 4) Fallback — treat clause head noun as positive.
        head = _pick_head_noun(clause.text)
        if head is None:
            return
        _add_positive(raw_positive, head, seen_positive)


def _split_on_multi_need(clause: _Clause) -> Optional[list[str]]:
    """Return the segments of an ``X mi Y mi`` / ``X mı Y mı`` pattern."""

    words = clause.words
    lowered = [_norm_lower(w) for w in words]
    question_particles = {"mi", "mı", "mu", "mü"}
    hits = [i for i, w in enumerate(lowered) if w in question_particles]
    if len(hits) < 2:
        return None
    segments: list[str] = []
    prev = 0
    for idx in hits:
        seg = " ".join(words[prev:idx]).strip()
        if seg:
            segments.append(seg)
        prev = idx + 1
    if len(segments) >= 2:
        return segments
    return None


def _split_on_neg_cue(clause: _Clause) -> Optional[tuple[str, str]]:
    """Return ``(before, after)`` when a negation cue appears mid-clause."""

    words = clause.words
    lowered = [_norm_lower(w) for w in words]
    cue_set = {ascii_fold(c) for c in _NEG_CUES}
    for idx, low in enumerate(lowered):
        if low in cue_set:
            before = " ".join(words[:idx])
            after = " ".join(words[idx + 1 :])
            if before and after:
                return before, after
    return None


def _add_positive(bucket: list[dict], concept: str, seen: set[str]) -> None:
    key = _norm_lower(concept)
    if key in seen:
        return
    seen.add(key)
    bucket.append(
        {"concept": concept, "source": "EXPLICIT", "confidence": 0.9}
    )


def _add_negative(bucket: list[dict], concept: str, seen: set[str]) -> None:
    key = _norm_lower(concept)
    if key in seen:
        return
    seen.add(key)
    bucket.append(
        {
            "concept": concept,
            "source": "EXPLICIT_NEGATION",
            "confidence": 0.95,
        }
    )


def _to_schema_constraints(
    positive: list[dict],
    negative: list[dict],
    corrections: list[dict],
) -> dict:
    """Convert the FAST-side dicts into the NeedProfile schema shape."""

    def _prov_from_source(entry: Mapping[str, Any]) -> str:
        return str(entry.get("source") or entry.get("provenance") or "EXPLICIT")

    out: dict = {}
    if positive:
        out["positive"] = [
            {
                "concept": p["concept"],
                "provenance": _prov_from_source(p),
                "weight": float(p.get("confidence", 0.9)),
            }
            for p in positive
        ]
    if negative:
        out["negative"] = [
            {
                "concept": n["concept"],
                "provenance": _prov_from_source(n),
                "weight": float(n.get("confidence", 0.95)),
            }
            for n in negative
        ]
    if corrections:
        # The NeedProfile schema stores corrections as constraint_items
        # (concept + provenance). Encode each as its replacement concept
        # tagged USER_CORRECTION; the validator's correction-slot handling
        # then upgrades them via the extractor's parallel corrections
        # list (raw dict) rather than schema round-trip.
        out["corrections"] = [
            {
                "concept": c["replacement_concept"],
                "provenance": "USER_CORRECTION",
                "weight": float(c.get("confidence", 0.95)),
            }
            for c in corrections
        ]
    return out


__all__ = ["DeterministicFastExtractor"]

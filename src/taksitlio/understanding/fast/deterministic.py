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
from taksitlio.understanding.normalization.morphology_safe import (
    TurkishMorphologySafeNormalizer,
    pick_surface_head_noun,
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
    "arıyorum", "arıyoruz", "arıyor", "bakıyorum", "bakıyoruz", "bakayım",
    "istiyorum", "istiyoruz", "istemiyorum", "istemem",
    "istemiyor", "istemedim", "istemedik", "gerekiyor", "gerekmiyor",
    "olsun", "olabilir", "olur", "yeter", "lazım", "gerek",
    "alacağım", "alacağız", "alcağım", "alacak", "alalım",
    "alsam", "alsak", "alsınlar", "aldık",
    "almalıyım", "almalıyız", "almalı", "almalısın", "almalısınız",
    "aldım", "verir", "verin", "verirsiniz", "önerin", "önerir",
    "önerebilir", "tavsiye", "modelleri", "modellerine",
    "önce", "sonra", "biraz", "çok", "az",
    # Pronouns
    "ben", "sen", "biz", "siz", "onlar", "kendi", "kendim",
    "hepsi", "birbirimize",
    # Politeness / apology / affirmation — never a category concept.
    "lütfen", "teşekkürler", "sağolun", "özür", "dilerim", "üzgünüm",
    "pardon", "evet", "tamam", "tabi", "tabii",
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
    # Additional verbs / adverbs used in correction / question forms.
    # "demedim" / "demiyorum" ALSO participate in _NEG_CUES so they split
    # a clause, but even when they don't split they must never leak as a
    # concept.
    "demedim", "demiyorum", "demiştim", "dedim", "dediysem", "diyecektim",
    "diyecek", "diyeceğim",
    "söyledim", "söylüyorum", "söyledin", "söyleyeceğim", "söyler",
    "yanlış", "yanlis", "anladın", "anladin", "anladım",
    "kastediyordum", "kastetmiştim", "kastettim", "kastediyor",
    "kastetmedim", "kastetmiyorum",
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
# Also includes "retract-and-replace" cues ("demedim" = "I didn't say
# [that]") so that ``X demedim Y dedim`` splits cleanly into negative X
# + positive Y (+ correction pair when a correction cue is present).
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
    "demedim",
    "demiyorum",
    "kastetmedim",
    "kastetmiyorum",
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

# Indecision cues — user asks the assistant to help pick between two
# concepts without committing. Presence of any of these must turn a
# resolved "X yerine mi Y" correction back into a multi-need signal so
# that the matcher lands on AMBIGUOUS instead of auto-selecting Y.
_INDECISION_CUES: tuple[str, ...] = (
    "karar veremedim",
    "karar veremiyorum",
    "kararsızım",
    "kararsizim",
    "emin değilim",
    "emin degilim",
    "hangisi daha iyi",
    "hangisini alacağımı bilmiyorum",
    "hangisini almalıyım",
    "yoksa",
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


def _pick_head_noun(span: str, *, head_only: bool = False) -> Optional[str]:
    """Return the primary *surface* noun concept in ``span`` (ADR-008).

    Aggressive suffix stripping is intentionally NOT applied here — that
    would turn ``masaüstü`` into ``masaüst`` and destroy exact-alias
    signals. Morphological alternatives are attached later via
    ``TurkishMorphologySafeNormalizer`` as variants only.
    """

    return pick_surface_head_noun(
        span, stopwords=_STOPWORDS, head_only=head_only
    )


_MORPH = TurkishMorphologySafeNormalizer()


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

        # Independent indecision signal: "karar veremedim" / "yoksa" / …
        # means the user is asking us to choose — never a definitive
        # correction. We defer application until after clause extraction
        # so we can promote the resolved negative back to a positive.
        indecisive = any(
            cue in normalized_full for cue in _INDECISION_CUES
        )

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

        # Indecision post-processing (ADR-007 §1). The user is asking
        # for guidance between two options — surface BOTH as positives
        # so the matcher's multi_need_signal fires and lands on
        # AMBIGUOUS instead of auto-selecting the resolved replacement.
        if indecisive:
            multi_need = True
            if (
                len(raw_positive) == 1
                and len(raw_negative) == 1
                and not raw_corrections
            ):
                neg = raw_negative[0]
                neg_concept = neg["concept"]
                if not any(
                    _norm_lower(p["concept"]) == _norm_lower(neg_concept)
                    for p in raw_positive
                ):
                    raw_positive.append(
                        {
                            "concept": neg_concept,
                            "source": "EXPLICIT",
                            "confidence": 0.7,
                        }
                    )
                # The user has NOT rejected the alternative — drop the
                # negative so the matcher does not hard-exclude it.
                raw_negative.clear()
                seen_negative.clear()

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

        # 0) Final "hayır X" / "yok X" retraction — the span AFTER the last
        # hayır/yok is the surviving positive; earlier product mentions become
        # negatives ("laptop dedim … hayır masaüstü").
        hayir_split = _split_final_hayir(clause)
        if hayir_split is not None:
            before, after = hayir_split
            pos = _pick_head_noun(after) or _surface_phrase(after)
            if pos:
                if before:
                    # All earlier head nouns become negatives / previous.
                    for span in _content_noun_spans(before):
                        neg = _pick_head_noun(span) or span
                        if neg and _norm_lower(neg) != _norm_lower(pos):
                            _add_negative(raw_negative, neg, seen_negative)
                            if is_correction:
                                raw_corrections.append(
                                    {
                                        "previous_concept": neg,
                                        "replacement_concept": pos,
                                        "confidence": 0.95,
                                        "previous_surface_form": neg,
                                        "replacement_surface_form": pos,
                                    }
                                )
                _add_positive(raw_positive, pos, seen_positive)
                return

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
            # 1a) Trailing contrast marker ("... televizyon değil"). The
            # marker only negates the immediately preceding noun; the
            # earlier tokens form a separate positive proposition.
            # Special case: "X dedim Y değil" / "X dedim Y Z değil" —
            # positive is the head before ``dedim``, negative is the full
            # span after ``dedim`` (surface-preserving multi-token).
            if prev and not repl:
                dedim_split = _split_dedim_before_degil(before)
                if dedim_split is not None:
                    pos_span, neg_span = dedim_split
                    pos_concept = _pick_head_noun(pos_span)
                    neg_concept = _pick_head_noun(neg_span) or _surface_phrase(neg_span)
                    if pos_concept and neg_concept and pos_concept != neg_concept:
                        if is_correction:
                            raw_corrections.append(
                                {
                                    "previous_concept": neg_concept,
                                    "replacement_concept": pos_concept,
                                    "confidence": 0.95,
                                    "previous_surface_form": neg_concept,
                                    "replacement_surface_form": pos_concept,
                                }
                            )
                        _add_positive(raw_positive, pos_concept, seen_positive)
                        _add_negative(raw_negative, neg_concept, seen_negative)
                        return
                split = _split_left_at_last_noun(before)
                if split is not None:
                    rest_span, last_noun_span = split
                    pos_concept = _pick_head_noun(rest_span)
                    neg_concept = _pick_head_noun(last_noun_span)
                    if pos_concept and neg_concept and pos_concept != neg_concept:
                        if is_correction:
                            raw_corrections.append(
                                {
                                    "previous_concept": neg_concept,
                                    "replacement_concept": pos_concept,
                                    "confidence": 0.95,
                                }
                            )
                        _add_positive(raw_positive, pos_concept, seen_positive)
                        _add_negative(raw_negative, neg_concept, seen_negative)
                        return
                _add_negative(raw_negative, prev, seen_negative)
                return
            # 1b) Leading contrast marker ("değil X" — atypical but
            # possible in fragments). Treat right side as positive.
            if repl and not prev:
                _add_positive(raw_positive, repl, seen_positive)
                return

        # 2) Explicit-negation cue within a single clause. Split on the
        # cue: everything before → negative concept; everything after →
        # positive concept ("telefon istemiyorum tablet bakıyorum" or
        # "telefon istemiyorum" as a standalone clause).
        neg_split = _split_on_neg_cue(clause)
        if neg_split is not None:
            before, after = neg_split
            neg_concept = _pick_head_noun(before) if before else None
            pos_concept = _pick_head_noun(after) if after else None
            # Retract-and-replace ("X demedim Y dedim") + explicit
            # correction cue → also emit a correction pair.
            if (
                is_correction
                and neg_concept
                and pos_concept
                and neg_concept != pos_concept
            ):
                raw_corrections.append(
                    {
                        "previous_concept": neg_concept,
                        "replacement_concept": pos_concept,
                        "confidence": 0.95,
                    }
                )
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
    """Return ``(before, after)`` when a negation cue appears in the clause.

    Prefers a cue whose split leaves *content* (non-stopword tokens) on
    BOTH sides — that's the "yok telefonu boşver bilgisayar" or the
    "X demedim Y dedim" shape where the user names both what they don't
    want and what they do want. Falls back to a one-sided split when
    only one cue exists (``"telefon istemiyorum"`` → negative-only).
    """

    words = clause.words
    lowered = [_norm_lower(w) for w in words]
    cue_set = {ascii_fold(c) for c in _NEG_CUES}
    matches = [i for i, low in enumerate(lowered) if low in cue_set]
    if not matches:
        return None

    def _has_content(span: str) -> bool:
        return any(
            _norm_lower(w) not in _STOPWORDS for w in _tokenize(span)
        )

    # First pass: prefer bilateral content splits.
    for idx in matches:
        before = " ".join(words[:idx])
        after = " ".join(words[idx + 1 :])
        if _has_content(before) and _has_content(after):
            return before, after

    # Fallback: any non-empty side is enough.
    for idx in matches:
        before = " ".join(words[:idx])
        after = " ".join(words[idx + 1 :])
        if before or after:
            return before, after
    return None


def _split_left_at_last_noun(left_span: str) -> Optional[tuple[str, str]]:
    """Split ``left_span`` into ``(rest, last_noun_span)``.

    Used when a contrast marker ("değil" / "yerine") sits at the END of
    the clause (``"ses sistemi lazım televizyon değil"``) — the marker
    then negates ONLY the immediately preceding noun, and everything
    before that noun is a separate positive proposition.

    Returns None when ``left_span`` does not contain at least two
    non-stopword tokens.
    """

    if not left_span:
        return None
    words = _tokenize(left_span)
    if not words:
        return None
    content_idxs = [i for i, w in enumerate(words) if _norm_lower(w) not in _STOPWORDS]
    if len(content_idxs) < 2:
        return None
    last_idx = content_idxs[-1]
    last_noun_span = words[last_idx]
    rest_span = " ".join(words[:last_idx])
    return rest_span.strip(), last_noun_span.strip()


_DEDIM_CUES = frozenset(
    {
        "dedim",
        "dedik",
        "dediysem",
        "soyledim",
        "söyledim",
        "diyorum",
    }
)


def _split_dedim_before_degil(before: str) -> Optional[tuple[str, str]]:
    """``X dedim Y`` → (X, Y) for trailing-değil correction clauses."""

    words = _tokenize(before)
    if not words:
        return None
    lowered = [_norm_lower(w) for w in words]
    for idx, low in enumerate(lowered):
        if low not in _DEDIM_CUES:
            continue
        left = " ".join(words[:idx]).strip()
        right = " ".join(words[idx + 1 :]).strip()
        if left and right:
            return left, right
    return None


def _surface_phrase(span: str) -> Optional[str]:
    """Keep a multi-token surface phrase after dropping stopwords only."""

    words = _tokenize(span)
    keep = [w for w in words if _norm_lower(w) not in _STOPWORDS]
    if not keep:
        return None
    phrase = " ".join(keep).strip()
    return phrase if len(phrase) >= 2 else None


_HAYIR_CUES = frozenset({"hayir", "hayır"})


def _split_final_hayir(clause: _Clause) -> Optional[tuple[str, str]]:
    """Split on the last ``hayır`` / ``yok`` cue → (before, after)."""

    words = clause.words
    if not words:
        return None
    lowered = [_norm_lower(w) for w in words]
    last_idx = None
    for idx, low in enumerate(lowered):
        if low in _HAYIR_CUES:
            last_idx = idx
    if last_idx is None:
        return None
    before = " ".join(words[:last_idx]).strip()
    after = " ".join(words[last_idx + 1 :]).strip()
    if not after or not before:
        # Leading ``hayır …`` is handled by demedim/contrast paths.
        return None
    return before, after


def _content_noun_spans(span: str) -> list[str]:
    """Rough content-noun spans separated by soft punctuation / conjunctions."""

    parts = re.split(r"[,;/]| ama | fakat | aslinda | aslında ", span or "")
    out: list[str] = []
    for part in parts:
        phrase = _surface_phrase(part)
        if phrase:
            out.append(phrase)
    if not out and span.strip():
        head = _pick_head_noun(span)
        if head:
            out.append(head)
    return out


def _add_positive(bucket: list[dict], concept: str, seen: set[str]) -> None:
    _emit_constraint(bucket, concept, seen, source="EXPLICIT", confidence=0.9)


def _add_negative(bucket: list[dict], concept: str, seen: set[str]) -> None:
    _emit_constraint(
        bucket, concept, seen, source="EXPLICIT_NEGATION", confidence=0.95
    )
    # Also emit the bare head-noun surface as a sibling negative so aliases
    # like ``telefon`` still hard-exclude when the user said "yeni telefon".
    tokens = _tokenize(concept)
    if len(tokens) > 1:
        head_only = _pick_head_noun(concept, head_only=True)
        if head_only:
            _emit_constraint(
                bucket,
                head_only,
                seen,
                source="EXPLICIT_NEGATION",
                confidence=0.9,
            )


def _emit_constraint(
    bucket: list[dict],
    concept: str,
    seen: set[str],
    *,
    source: str,
    confidence: float,
) -> None:
    normalized = _MORPH.normalize_concept(concept)
    primary = normalized.primary
    if not primary:
        return
    key = _norm_lower(primary)
    if key in seen:
        return
    seen.add(key)
    payload = {
        "concept": primary,
        "source": source,
        "confidence": confidence,
        **normalized.to_constraint_fields(),
    }
    # to_constraint_fields already has concept — keep source for validator.
    payload["concept"] = primary
    payload["source"] = source
    payload["confidence"] = confidence
    bucket.append(payload)


def _to_schema_constraints(
    positive: list[dict],
    negative: list[dict],
    corrections: list[dict],
) -> dict:
    """Convert the FAST-side dicts into the NeedProfile schema shape."""

    def _item(entry: Mapping[str, Any], *, default_prov: str, default_w: float) -> dict:
        item = {
            "concept": entry["concept"],
            "provenance": str(entry.get("source") or entry.get("provenance") or default_prov),
            "weight": float(entry.get("confidence", default_w)),
        }
        for key in (
            "surface_form",
            "normalized_form",
            "variants",
            "normalization_source",
        ):
            if entry.get(key):
                item[key] = entry[key]
        return item

    out: dict = {}
    if positive:
        out["positive"] = [_item(p, default_prov="EXPLICIT", default_w=0.9) for p in positive]
    if negative:
        out["negative"] = [
            _item(n, default_prov="EXPLICIT_NEGATION", default_w=0.95) for n in negative
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
                **(
                    {"surface_form": c["replacement_surface_form"]}
                    if c.get("replacement_surface_form")
                    else {}
                ),
            }
            for c in corrections
            if c.get("replacement_concept")
        ]
    return out


__all__ = ["DeterministicFastExtractor"]

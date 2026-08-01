"""Export NeedProfile SFT rows from existing goldens (ADR-009 / P17).

Does not invent merchant/bank names or claim quality pass.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

from taksitlio.understanding.fast.schema_utils import (
    build_empty_need_profile,
    validate_need_profile,
)

# Keep aligned with FAST remote extractor (constraint-boost): HR100 scores
# semantic_constraints.positive/negative, not category_hint preferences alone.
DEFAULT_SYSTEM_PROMPT = """You extract Turkish purchase needs as one compact JSON object only (NeedProfile).
Rules:
- Output minified JSON on one logical object: no markdown, no pretty-print, no extra whitespace.
- Keep need_description <= 120 chars; keep arrays short (prefer empty over filler).
- Never emit category IDs, fixture keys, or UUIDs.
- Never invent facts not present in the user utterance (no external knowledge).
- Do not answer general chat, weather, homework, translation, politics, or open-world Q&A;
  set intent.type=OUT_OF_SCOPE for those.
- intent: {type, confidence}; type enum PRODUCT_PURCHASE|COMPARE_OPTIONS|BUDGET_INQUIRY|INSTALLMENT_INQUIRY|OUT_OF_SCOPE|CLARIFICATION_RESPONSE|OTHER
- need_description: short Turkish string from the utterance
- budget: {type, value, minimum, maximum, monthly_payment, currency}; type UNKNOWN/EXACT/APPROXIMATE/RANGE/MONTHLY_PAYMENT; currency TRY; unused numerics null
- preferences: [{concept, importance}] — positive wants only
- usage_context: string array (usually empty)
- entities: [{type, value, confidence?}]
- ambiguities: [{code, description}] (usually empty)
- clarification: {required, question_intent}
- confidence: 0..1
- semantic_constraints: {positive, negative, corrections} each [{concept, provenance, weight?}]; provenance EXPLICIT|INFERRED|EXPLICIT_NEGATION|USER_CORRECTION|SESSION_CONTEXT
CRITICAL for Turkish utterances:
- Every wanted product/concept MUST appear in semantic_constraints.positive with provenance EXPLICIT.
- Every rejected/excluded product (istemiyorum, değil, boşver, yerine) MUST appear in semantic_constraints.negative with provenance EXPLICIT_NEGATION.
- If the user corrects (yanlış, demedim, özür, değil X lazım Y): add corrections with previous_concept+replacement_concept when possible; also put Y in positive and X in negative.
- Put exclusions ONLY in semantic_constraints.negative — never as low-importance preferences.
No markdown."""

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_GOLDEN = _REPO_ROOT / "eval" / "golden" / "tr_need_understanding.jsonl"
_DEFAULT_HR = (
    _REPO_ROOT / "evaluation" / "datasets" / "validation" / "tr-category-validation.v4.jsonl"
)


def need_profile_from_golden_expected(
    utterance: str, expected: Mapping[str, Any]
) -> dict[str, Any]:
    """Map compact golden ``expected`` → schema-valid NeedProfile."""

    intent = (expected.get("intent") or {}).get("type") or "PRODUCT_PURCHASE"
    profile = build_empty_need_profile(utterance=utterance, intent=str(intent))
    budget_in = expected.get("budget") or {}
    if isinstance(budget_in, Mapping) and budget_in:
        btype = str(budget_in.get("type") or "UNKNOWN")
        profile["budget"] = {
            "type": btype if btype in {
                "UNKNOWN", "EXACT", "APPROXIMATE", "RANGE", "MONTHLY_PAYMENT"
            } else "APPROXIMATE",
            "value": budget_in.get("value"),
            "minimum": budget_in.get("minimum"),
            "maximum": budget_in.get("maximum"),
            "monthly_payment": budget_in.get("monthly_payment"),
            "currency": budget_in.get("currency") or "TRY",
        }
    prefs: list[dict[str, Any]] = []
    positive: list[dict[str, Any]] = []
    for concept in expected.get("preferences") or []:
        c = str(concept)
        prefs.append({"concept": c, "importance": 0.8})
        positive.append({"concept": c, "provenance": "EXPLICIT", "weight": 0.8})
    hint = expected.get("category_hint")
    if hint:
        # Opaque concept label only — not a DB category id.
        prefs.append({"concept": f"category_hint:{hint}", "importance": 0.7})
        # Surface concept for constraint recall (lowercase hint token).
        surface = str(hint).strip().lower().replace("_", " ")
        if surface and not any(p["concept"] == surface for p in positive):
            positive.append(
                {"concept": surface, "provenance": "EXPLICIT", "weight": 0.7}
            )
    profile["preferences"] = prefs
    if expected.get("clarify"):
        profile["clarification"] = {
            "required": True,
            "question_intent": "category",
        }
        profile["ambiguities"] = [
            {
                "code": "ambiguous_category",
                "description": "ambiguous_or_multiple",
            }
        ]
    signals = expected.get("signals")
    if isinstance(signals, Mapping) and signals:
        profile["signals"] = dict(signals)
    profile["semantic_constraints"] = {
        "positive": positive,
        "negative": [],
        "corrections": [],
    }
    profile["confidence"] = 0.9 if intent != "OUT_OF_SCOPE" else 0.95
    validate_need_profile(profile)
    return profile


def need_profile_from_hr_constraints(
    utterance: str, constraints: Mapping[str, Any]
) -> dict[str, Any]:
    """Build NeedProfile from HR ``semantic_constraints`` (concepts only)."""

    profile = build_empty_need_profile(utterance=utterance, intent="PRODUCT_PURCHASE")
    prefs: list[dict[str, Any]] = []
    for item in constraints.get("positive") or []:
        if isinstance(item, Mapping) and item.get("concept"):
            prefs.append(
                {
                    "concept": str(item["concept"]),
                    "importance": float(item.get("confidence") or 0.8),
                }
            )
    profile["preferences"] = prefs
    ambiguities: list[dict[str, Any]] = []
    for item in constraints.get("negative") or []:
        if isinstance(item, Mapping) and item.get("concept"):
            ambiguities.append(
                {
                    "code": "negative_constraint",
                    "description": f"exclude:{item['concept']}",
                }
            )
    for item in constraints.get("corrections") or []:
        if isinstance(item, Mapping) and item.get("concept"):
            ambiguities.append(
                {
                    "code": "correction",
                    "description": f"correct:{item['concept']}",
                }
            )
    profile["ambiguities"] = ambiguities

    def _constraint_items(raw: object, *, default_prov: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not isinstance(raw, list):
            return out
        for i in raw:
            if not isinstance(i, Mapping) or not i.get("concept"):
                continue
            src = str(i.get("source") or default_prov).upper()
            if src in {"EXPLICIT_NEGATION"}:
                provenance = "EXPLICIT_NEGATION"
            elif src in {"USER_CORRECTION", "CORRECTION"}:
                provenance = "USER_CORRECTION"
            elif src in {"INFERRED", "SESSION_CONTEXT"}:
                provenance = src
            else:
                provenance = "EXPLICIT"
            out.append(
                {
                    "concept": str(i["concept"]),
                    "provenance": provenance,
                    "weight": float(i.get("confidence") or i.get("weight") or 0.8),
                }
            )
        return out

    profile["semantic_constraints"] = {
        "positive": _constraint_items(
            constraints.get("positive"), default_prov="EXPLICIT"
        ),
        "negative": _constraint_items(
            constraints.get("negative"), default_prov="EXPLICIT_NEGATION"
        ),
        "corrections": _constraint_items(
            constraints.get("corrections"), default_prov="USER_CORRECTION"
        ),
    }
    profile["confidence"] = 0.92
    validate_need_profile(profile)
    return profile


def build_sft_row(
    *,
    case_id: str,
    utterance: str,
    need_profile: Mapping[str, Any],
    source_path: str,
    annotation_status: str,
    locale: str = "tr-TR",
    split: str = "train",
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> dict[str, Any]:
    assistant = json.dumps(need_profile, ensure_ascii=False, separators=(",", ":"))
    return {
        "id": case_id,
        "utterance": utterance,
        "locale": locale,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": utterance},
            {"role": "assistant", "content": assistant},
        ],
        "need_profile": dict(need_profile),
        "source_ref": {"path": source_path, "case_id": case_id},
        "annotation_status": annotation_status,
        "split": split,
    }


def iter_golden_sft_rows(
    path: Path | None = None,
    *,
    limit: Optional[int] = None,
) -> Iterator[dict[str, Any]]:
    src = path or _DEFAULT_GOLDEN
    n = 0
    with src.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            utterance = str(row.get("message") or "")
            case_id = str(row.get("id") or f"golden-{n}")
            profile = need_profile_from_golden_expected(
                utterance, row.get("expected") or {}
            )
            yield build_sft_row(
                case_id=case_id,
                utterance=utterance,
                need_profile=profile,
                source_path=str(src.as_posix()),
                annotation_status="SYNTHETIC",
                split="train",
            )
            n += 1
            if limit is not None and n >= limit:
                return


def iter_hr_validation_sft_rows(
    path: Path | None = None,
    *,
    limit: Optional[int] = None,
    human_reviewed_only: bool = True,
    draft_only: bool = False,
    exclude_human_reviewed: bool = False,
) -> Iterator[dict[str, Any]]:
    """Export HR/validation rows as SFT.

    For honest HR100 eval, prefer ``draft_only=True`` (or
    ``exclude_human_reviewed=True``) so HUMAN_REVIEWED val cases are not
    memorized during train.
    """

    src = path or _DEFAULT_HR
    n = 0
    with src.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            status = ((row.get("annotation") or {}).get("status") or "").upper()
            if draft_only and status != "DRAFT":
                continue
            if exclude_human_reviewed and status == "HUMAN_REVIEWED":
                continue
            if human_reviewed_only and not draft_only and not exclude_human_reviewed:
                if status != "HUMAN_REVIEWED":
                    continue
            utterance = str(row.get("utterance") or "")
            case_id = str(row.get("case_id") or f"hr-{n}")
            profile = need_profile_from_hr_constraints(
                utterance, row.get("semantic_constraints") or {}
            )
            # Hold out *val* HUMAN_REVIEWED for eval; drafts may train.
            if status == "HUMAN_REVIEWED" and "val" in case_id:
                split = "eval"
            else:
                split = "train"
            yield build_sft_row(
                case_id=case_id,
                utterance=utterance,
                need_profile=profile,
                source_path=str(src.as_posix()),
                annotation_status="HUMAN_REVIEWED" if status == "HUMAN_REVIEWED" else "DRAFT",
                locale=str(row.get("locale") or "tr-TR"),
                split=split,
            )
            n += 1
            if limit is not None and n >= limit:
                return


def write_jsonl(rows: Sequence[Mapping[str, Any]], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "build_sft_row",
    "iter_golden_sft_rows",
    "iter_hr_validation_sft_rows",
    "need_profile_from_golden_expected",
    "need_profile_from_hr_constraints",
    "write_jsonl",
]

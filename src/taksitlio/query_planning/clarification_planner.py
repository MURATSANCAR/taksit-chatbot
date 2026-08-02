"""Generate user-facing Turkish clarification questions for ambiguous plans."""

from __future__ import annotations

from typing import Any

from taksitlio.query_planning.models import CanonicalSearchPlan, RequestType


def _budget_stretch_question(plan: CanonicalSearchPlan) -> dict[str, Any] | None:
    gc = plan.global_constraints
    if not gc or not gc.budget:
        return None
    b = gc.budget
    if b.stretch_maximum and b.target_maximum:
        return {
            "question_type": "budget_stretch",
            "text": (
                f"Bütçenizi {b.target_maximum:,.0f} {b.currency} olarak belirlediniz. "
                f"Eğer çok iyi bir fırsat varsa {b.stretch_maximum:,.0f} {b.currency}'ye "
                f"kadar çıkmayı düşünür müsünüz?"
            ),
            "options": [
                {"label": "Evet, fırsat varsa esnetilebilir", "action": "accept_stretch"},
                {"label": f"Hayır, kesinlikle {b.target_maximum:,.0f} {b.currency} üstü istemiyorum", "action": "reject_stretch"},
            ],
        }
    return None


def _multi_category_question(plan: CanonicalSearchPlan) -> dict[str, Any] | None:
    if plan.request_type != RequestType.MULTI_ITEM_BUNDLE:
        return None
    if len(plan.items) < 2:
        return None

    cat_names = []
    for item in plan.items:
        if item.category and (item.category.raw_text or item.category.resolved_id):
            cat_names.append(item.category.raw_text or item.category.resolved_id or "")

    if len(cat_names) < 2:
        return None

    joined = " ve ".join(cat_names)
    return {
        "question_type": "multi_category",
        "text": (
            f"{joined} için ayrı ayrı mı arama yapmamı istersiniz, "
            f"yoksa hepsini tek bir bütçe içinde mi değerlendirelim?"
        ),
        "options": [
            {"label": "Ayrı ayrı ara", "action": "separate_searches"},
            {"label": "Tek bütçede birleştir", "action": "bundle_search"},
        ],
    }


def _conflict_question(plan: CanonicalSearchPlan) -> dict[str, Any] | None:
    if not plan.conflicts:
        return None
    first = plan.conflicts[0]
    dim = first.get("dimension", "kısıtlama")
    return {
        "question_type": "conflict_resolution",
        "text": (
            f"'{dim}' boyutunda çelişen kısıtlamalar tespit ettim. "
            f"Hangisini önceliklendirmemi istersiniz?"
        ),
        "conflict_ref": first,
    }


def _ambiguity_question(plan: CanonicalSearchPlan) -> dict[str, Any] | None:
    if not plan.ambiguities:
        return None
    return {
        "question_type": "ambiguity",
        "text": plan.ambiguities[0],
    }


_QUESTION_BUILDERS = [
    _conflict_question,
    _budget_stretch_question,
    _multi_category_question,
    _ambiguity_question,
]


def build_clarification_questions(
    plan: CanonicalSearchPlan,
    *,
    max_questions: int = 2,
) -> list[dict[str, Any]]:
    """Return up to *max_questions* user-facing Turkish clarification questions."""
    questions: list[dict[str, Any]] = []
    for builder in _QUESTION_BUILDERS:
        if len(questions) >= max_questions:
            break
        q = builder(plan)
        if q is not None:
            questions.append(q)

    plan.clarification_questions = questions
    if questions:
        plan.clarification_required = True

    return questions

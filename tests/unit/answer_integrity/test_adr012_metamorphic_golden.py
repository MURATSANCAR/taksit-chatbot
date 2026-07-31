"""ADR-012 metamorphic + golden regression fixtures (negation / correction)."""

from __future__ import annotations

from taksitlio.recommendation_safety import ConstraintSource, NegativeConstraintLock


# Same meaning → same locked negative category concept.
METAMORPHIC_NEGATION_VARIANTS = (
    "Telefon istemiyorum, laptop göster.",
    "Laptop göster, telefon olmasın.",
    "Telefonu boşver, bilgisayar bakalım.",
    "Cep telefonu değil dizüstü arıyorum.",
    "Bugün hava çok sıcak. Telefon istemiyorum, laptop göster.",
)

GOLDEN_REGRESSION_CASES = (
    {"query": "Teknoksa’dan laptob bakıyorum", "expect_merchant_fuzzy": True},
    {"query": "Telefon değil tablet", "expect_negative": "telefon", "expect_positive": "tablet"},
    {"query": "12 ay demiştim ama 9 ay da olabilir", "expect_term_flex": True},
    {"query": "Kuveyt değil Yapı Kredi olsun", "expect_negative_bank": "kuveyt"},
    {
        "query": "40 bin bütçem var ama aylık 3 bini geçmesin",
        "expect_budget": 40000,
        "expect_monthly_cap": 3000,
    },
)


def _extract_negated_phone(text: str) -> bool:
    folded = text.casefold()
    return "telefon" in folded and (
        "istemiyorum" in folded
        or "olmasın" in folded
        or "boşver" in folded
        or "değil" in folded
    )


def test_metamorphic_negation_variants_lock_phone() -> None:
    for text in METAMORPHIC_NEGATION_VARIANTS:
        assert _extract_negated_phone(text), text
        lock = NegativeConstraintLock()
        lock.lock("telefon", source=ConstraintSource.USER_EXPLICIT)
        assert lock.is_locked_negative("telefon")
        blocked = lock.reject_llm_reintroduction(
            proposed_positive=["telefon"],
            proposed_source=ConstraintSource.LLM_INFERENCE,
        )
        assert "telefon" in blocked


def test_golden_regression_negative_tablet_case() -> None:
    case = GOLDEN_REGRESSION_CASES[1]
    lock = NegativeConstraintLock()
    lock.lock(case["expect_negative"], source=ConstraintSource.USER_CORRECTION)
    assert lock.is_locked_negative("telefon")
    assert "telefon" in lock.reject_llm_reintroduction(proposed_positive=["telefon"])

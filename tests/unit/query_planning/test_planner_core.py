"""Core tests for the query_planning package."""

from __future__ import annotations

import copy

import pytest

from taksitlio.query_planning.bundle import BundleResult, solve_bundle
from taksitlio.query_planning.capability_checker import check_capabilities
from taksitlio.query_planning.clarification_planner import build_clarification_questions
from taksitlio.query_planning.conflict_resolver import resolve_conflicts
from taksitlio.query_planning.executor import filter_products_by_plan, score_product_for_plan
from taksitlio.query_planning.models import (
    PLAN_VERSION,
    BudgetConstraint,
    CampaignIntent,
    CanonicalSearchPlan,
    CategoryRef,
    ConditionalException,
    ConstraintOperator,
    ConstraintStrength,
    GlobalConstraints,
    PlanConstraint,
    PlanItem,
    PlanSource,
    RequestType,
    StateOperation,
)
from taksitlio.query_planning.normalizer import normalize_fast_parse_to_plan
from taksitlio.query_planning.planner import (
    build_plan_from_fast_parse,
    detect_complex_route,
    merge_llm_plan_patch,
    plan_to_constraints_dict,
)
from taksitlio.query_planning.state_reducer import StaleVersionError, apply_operation
from taksitlio.query_planning.validator import PlanValidationError, validate_plan


# ── Fixtures ──────────────────────────────────────────────────────


def _laptop_parse() -> dict:
    return {
        "intent": "PRODUCT_SEARCH",
        "positive_categories": [
            {"resolved_id": "cat_laptop", "display_name": "Laptop", "confidence": 0.95, "required": False}
        ],
        "negative_categories": [],
        "brands": [],
        "budget": {"maximum": 30000, "currency": "TRY"},
        "attributes": [],
        "requested_terms": [],
        "ranking_mode": None,
        "merchant": None,
        "preferred_institutions": [],
        "route": "FAST_PATH",
    }


def _multi_category_parse() -> dict:
    return {
        "intent": "PRODUCT_SEARCH",
        "positive_categories": [
            {"resolved_id": "cat_laptop", "display_name": "Laptop", "confidence": 0.9, "required": False},
            {"resolved_id": "cat_mouse", "display_name": "Mouse", "confidence": 0.85, "required": False},
        ],
        "negative_categories": [],
        "brands": [],
        "budget": {"maximum": 35000, "currency": "TRY"},
        "attributes": [],
        "requested_terms": [],
        "ranking_mode": None,
        "merchant": None,
        "preferred_institutions": [],
        "route": "FAST_PATH",
    }


def _valid_plan_dict(**overrides: object) -> dict:
    base = {
        "plan_version": "v1",
        "request_type": "SINGLE_PRODUCT_SEARCH",
        "items": [
            {
                "item_id": "item-001",
                "category": {"resolved_id": "cat_laptop", "raw_text": "Laptop", "confidence": 0.9},
                "hard_constraints": [
                    {
                        "constraint_id": "c-brand-1",
                        "dimension": "brand",
                        "operator": "EQ",
                        "value": "BrandX",
                        "strength": "HARD",
                        "priority": 1,
                        "source_text": "BrandX",
                        "confidence": 0.9,
                        "source": "fast_parser",
                        "scope": "item",
                    }
                ],
                "soft_preferences": [],
                "excluded_constraints": [],
                "conditional_exceptions": [],
                "ranking_priorities": [],
                "unsupported_dimensions": [],
            }
        ],
        "global_constraints": {
            "budget": {"target_maximum": 30000, "currency": "TRY"},
            "allowed_merchants": [],
            "excluded_merchants": [],
        },
    }
    base.update(overrides)
    return base


# ── 1. Simple laptop budget → single plan ─────────────────────────


class TestSimpleLaptopPlan:
    def test_normalizes_to_single_product(self):
        plan = normalize_fast_parse_to_plan(_laptop_parse())
        assert plan.request_type == RequestType.SINGLE_PRODUCT_SEARCH
        assert len(plan.items) == 1
        assert plan.items[0].category is not None
        assert plan.items[0].category.resolved_id == "cat_laptop"

    def test_budget_mapped_to_target_maximum(self):
        plan = normalize_fast_parse_to_plan(_laptop_parse())
        assert plan.global_constraints is not None
        assert plan.global_constraints.budget is not None
        assert plan.global_constraints.budget.target_maximum == 30000
        assert plan.global_constraints.budget.stretch_maximum is None

    def test_plan_version(self):
        plan = normalize_fast_parse_to_plan(_laptop_parse())
        assert plan.plan_version == PLAN_VERSION

    def test_roundtrip_to_dict_from_dict(self):
        plan = normalize_fast_parse_to_plan(_laptop_parse())
        d = plan.to_dict()
        restored = CanonicalSearchPlan.from_dict(d)
        assert restored.plan_version == plan.plan_version
        assert restored.request_type == plan.request_type
        assert len(restored.items) == len(plan.items)

    def test_plan_to_constraints_dict(self):
        plan = normalize_fast_parse_to_plan(_laptop_parse())
        cd = plan_to_constraints_dict(plan)
        assert cd["plan_version"] == "v1"
        assert cd["budget"]["maximum"] == 30000
        assert len(cd["positive_categories"]) == 1


# ── 2. Hard / soft mix ────────────────────────────────────────────


class TestHardSoftMix:
    def test_required_brand_becomes_hard(self):
        parse = _laptop_parse()
        parse["brands"] = [
            {"resolved_id": "brand_x", "display_name": "BrandX", "confidence": 0.9, "required": True}
        ]
        plan = normalize_fast_parse_to_plan(parse)
        item = plan.items[0]
        hard_dims = [c.dimension for c in item.hard_constraints]
        assert "brand" in hard_dims

    def test_preferred_brand_becomes_soft(self):
        parse = _laptop_parse()
        parse["brands"] = [
            {"resolved_id": "brand_y", "display_name": "BrandY", "confidence": 0.8, "required": False}
        ]
        plan = normalize_fast_parse_to_plan(parse)
        item = plan.items[0]
        soft_dims = [c.dimension for c in item.soft_preferences]
        assert "brand" in soft_dims

    def test_required_attribute_becomes_hard(self):
        parse = _laptop_parse()
        parse["attributes"] = [
            {"dimension": "ram", "value": 16, "unit": "GB", "required": True, "operator": "GTE"}
        ]
        plan = normalize_fast_parse_to_plan(parse)
        item = plan.items[0]
        hard_dims = [c.dimension for c in item.hard_constraints]
        assert "ram" in hard_dims
        ram_c = [c for c in item.hard_constraints if c.dimension == "ram"][0]
        assert ram_c.operator == ConstraintOperator.GTE


# ── 3. Conditional stretch budget from message ────────────────────


class TestStretchBudget:
    def test_stretch_detected_from_turkish_signal(self):
        plan = normalize_fast_parse_to_plan(
            _laptop_parse(),
            message="30000 TL bütçem var ama biraz çıkabilir",
        )
        assert plan.global_constraints is not None
        budget = plan.global_constraints.budget
        assert budget is not None
        assert budget.stretch_maximum is not None
        assert budget.stretch_maximum > budget.target_maximum

    def test_no_stretch_without_signal(self):
        plan = normalize_fast_parse_to_plan(
            _laptop_parse(),
            message="30000 TL bütçem var",
        )
        budget = plan.global_constraints.budget
        assert budget.stretch_maximum is None

    def test_stretch_with_civarinda(self):
        plan = normalize_fast_parse_to_plan(
            _laptop_parse(),
            message="30000 civarında bir laptop arıyorum",
        )
        budget = plan.global_constraints.budget
        assert budget.stretch_maximum is not None


# ── 4. Negation exclusion ─────────────────────────────────────────


class TestNegationExclusion:
    def test_negative_categories_become_excluded(self):
        parse = _laptop_parse()
        parse["negative_categories"] = [
            {"resolved_id": "cat_tablet", "display_name": "Tablet", "confidence": 0.88}
        ]
        plan = normalize_fast_parse_to_plan(parse)
        item = plan.items[0]
        excl_dims = [c.dimension for c in item.excluded_constraints]
        assert "category" in excl_dims
        excl_vals = [c.value for c in item.excluded_constraints]
        assert "cat_tablet" in excl_vals


# ── 5. Multi-item detection ──────────────────────────────────────


class TestMultiItemDetection:
    def test_two_categories_yield_multi_bundle(self):
        plan = normalize_fast_parse_to_plan(_multi_category_parse())
        assert plan.request_type == RequestType.MULTI_ITEM_BUNDLE
        assert len(plan.items) == 2

    def test_single_category_stays_single(self):
        plan = normalize_fast_parse_to_plan(_laptop_parse())
        assert plan.request_type == RequestType.SINGLE_PRODUCT_SEARCH

    def test_detect_complex_with_multi_categories(self):
        assert detect_complex_route(_multi_category_parse(), "") is True

    def test_simple_parse_not_complex(self):
        assert detect_complex_route(_laptop_parse(), "") is False


# ── 6. Forbidden field rejection ─────────────────────────────────


class TestForbiddenFieldRejection:
    def test_rejects_product_id_in_value(self):
        d = _valid_plan_dict()
        d["items"][0]["hard_constraints"][0]["value"] = "product_id"
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(d)
        assert any("product_id" in e.lower() for e in exc_info.value.errors)

    def test_rejects_forbidden_key(self):
        d = _valid_plan_dict()
        d["api_key"] = "some_secret"
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(d)
        assert any("api_key" in e.lower() for e in exc_info.value.errors)

    def test_strict_rejects_unknown_field(self):
        d = _valid_plan_dict()
        d["totally_new_field"] = "hello"
        with pytest.raises(PlanValidationError):
            validate_plan(d, strict=True)

    def test_valid_plan_passes(self):
        d = _valid_plan_dict()
        plan = validate_plan(d)
        assert plan.plan_version == "v1"


# ── 7. RELAX and ROLLBACK state ops ──────────────────────────────


class TestStateOps:
    def _setup_plan_with_hard(self):
        plan = CanonicalSearchPlan(
            items=[PlanItem(
                item_id="item-1",
                hard_constraints=[
                    PlanConstraint(
                        constraint_id="c-brand-1",
                        dimension="brand",
                        operator=ConstraintOperator.EQ,
                        value="BrandX",
                        strength=ConstraintStrength.HARD,
                    )
                ],
            )],
        )
        state = {"state_version": 0}
        return state, plan.to_dict()

    def test_relax_moves_hard_to_soft(self):
        state, plan_dict = self._setup_plan_with_hard()
        new_state, new_plan, record = apply_operation(
            state, plan_dict, StateOperation.RELAX,
            target_constraint_id="c-brand-1",
            query_version=0,
        )
        plan = CanonicalSearchPlan.from_dict(new_plan)
        assert len(plan.items[0].hard_constraints) == 0
        assert any(c.constraint_id == "c-brand-1" for c in plan.items[0].soft_preferences)
        assert new_state["state_version"] == 1

    def test_rollback_restores_previous(self):
        state, plan_dict = self._setup_plan_with_hard()
        new_state, new_plan, record = apply_operation(
            state, plan_dict, StateOperation.RELAX,
            target_constraint_id="c-brand-1",
            query_version=0,
        )
        rolled_state, rolled_plan, _ = apply_operation(
            new_state, new_plan, StateOperation.ROLLBACK,
            query_version=1,
            history=[record],
        )
        plan = CanonicalSearchPlan.from_dict(rolled_plan)
        assert len(plan.items[0].hard_constraints) == 1
        assert plan.items[0].hard_constraints[0].constraint_id == "c-brand-1"

    def test_stale_version_raises(self):
        state, plan_dict = self._setup_plan_with_hard()
        with pytest.raises(StaleVersionError):
            apply_operation(
                state, plan_dict, StateOperation.RELAX,
                target_constraint_id="c-brand-1",
                query_version=99,
            )

    def test_require_promotes_soft_to_hard(self):
        plan = CanonicalSearchPlan(
            items=[PlanItem(
                item_id="item-1",
                soft_preferences=[
                    PlanConstraint(
                        constraint_id="c-color-1",
                        dimension="color",
                        operator=ConstraintOperator.EQ,
                        value="black",
                        strength=ConstraintStrength.SOFT,
                    )
                ],
            )],
        )
        state = {"state_version": 0}
        new_state, new_plan, _ = apply_operation(
            state, plan.to_dict(), StateOperation.REQUIRE,
            target_constraint_id="c-color-1",
            query_version=0,
        )
        result = CanonicalSearchPlan.from_dict(new_plan)
        assert len(result.items[0].soft_preferences) == 0
        assert any(c.constraint_id == "c-color-1" for c in result.items[0].hard_constraints)


# ── 8. Bundle solver respects global budget ──────────────────────


class TestBundleSolver:
    def test_within_budget(self):
        candidates = {
            "laptop": [
                {"product_id": "p1", "display_name": "Laptop A", "price": 20000},
                {"product_id": "p2", "display_name": "Laptop B", "price": 25000},
            ],
            "mouse": [
                {"product_id": "p3", "display_name": "Mouse A", "price": 500},
                {"product_id": "p4", "display_name": "Mouse B", "price": 1000},
            ],
        }
        result = solve_bundle(candidates, global_budget_max=22000)
        assert result.total_price <= 22000
        assert result.budget_remaining >= 0
        assert "laptop" in result.items
        assert "mouse" in result.items

    def test_over_budget_returns_cheapest(self):
        candidates = {
            "laptop": [
                {"product_id": "p1", "display_name": "Laptop", "price": 50000},
            ],
            "mouse": [
                {"product_id": "p2", "display_name": "Mouse", "price": 500},
            ],
        }
        result = solve_bundle(candidates, global_budget_max=10000)
        assert "OVER_BUDGET" in result.reason_codes[0]
        assert result.total_price > 10000

    def test_empty_candidates(self):
        result = solve_bundle({}, global_budget_max=10000)
        assert "NO_ITEMS" in result.reason_codes

    def test_finance_bundle_not_supported(self):
        candidates = {
            "item1": [{"product_id": "p1", "display_name": "X", "price": 100}],
        }
        result = solve_bundle(candidates, global_budget_max=200)
        assert result.finance_bundle == "NOT_SUPPORTED"

    def test_to_dict(self):
        candidates = {
            "item1": [{"product_id": "p1", "display_name": "X", "price": 100}],
        }
        result = solve_bundle(candidates, global_budget_max=200)
        d = result.to_dict()
        assert "items" in d
        assert "total_price" in d
        assert "finance_bundle" in d


# ── 9. Subjective unsupported dimension ──────────────────────────


class TestCapabilityChecker:
    def test_subjective_dim_flagged(self):
        plan = CanonicalSearchPlan(
            items=[PlanItem(
                item_id="item-1",
                soft_preferences=[
                    PlanConstraint(
                        constraint_id="c-quiet-1",
                        dimension="quiet",
                        operator=ConstraintOperator.EQ,
                        value="yes",
                        strength=ConstraintStrength.SOFT,
                    )
                ],
            )],
        )
        plan = check_capabilities(plan, catalog_dimensions={"brand", "ram", "storage"})
        assert "quiet" in plan.items[0].unsupported_dimensions
        assert any("quiet" in cap for cap in plan.unsupported_capabilities)

    def test_known_dim_not_flagged(self):
        plan = CanonicalSearchPlan(
            items=[PlanItem(
                item_id="item-1",
                soft_preferences=[
                    PlanConstraint(
                        constraint_id="c-ram-1",
                        dimension="ram",
                        operator=ConstraintOperator.GTE,
                        value=16,
                        strength=ConstraintStrength.SOFT,
                    )
                ],
            )],
        )
        plan = check_capabilities(plan, catalog_dimensions={"brand", "ram", "storage"})
        assert plan.items[0].unsupported_dimensions == []

    def test_finance_not_ready(self):
        plan = CanonicalSearchPlan(
            campaign_intent=CampaignIntent(requested=True),
            items=[PlanItem(item_id="item-1")],
        )
        plan = check_capabilities(plan, finance_ready=False)
        assert "FINANCE_NOT_READY" in plan.unsupported_capabilities


# ── 10. Conflict resolver ────────────────────────────────────────


class TestConflictResolver:
    def test_hard_excluded_same_dimension_detected(self):
        plan = CanonicalSearchPlan(
            items=[PlanItem(
                item_id="item-1",
                hard_constraints=[
                    PlanConstraint(
                        constraint_id="c-cat-1",
                        dimension="category",
                        operator=ConstraintOperator.EQ,
                        value="cat_laptop",
                        strength=ConstraintStrength.HARD,
                    )
                ],
                excluded_constraints=[
                    PlanConstraint(
                        constraint_id="c-cat-2",
                        dimension="category",
                        operator=ConstraintOperator.NEQ,
                        value="cat_tablet",
                        strength=ConstraintStrength.HARD,
                    )
                ],
            )],
        )
        plan = resolve_conflicts(plan)
        assert len(plan.conflicts) >= 1
        assert plan.conflicts[0]["dimension"] == "category"


# ── 11. build_plan_from_fast_parse integration ───────────────────


class TestBuildPlanIntegration:
    def test_full_pipeline(self):
        plan = build_plan_from_fast_parse(
            _laptop_parse(),
            message="30000 TL bütçem var ama biraz çıkabilir",
            catalog_dimensions={"brand", "ram", "storage"},
        )
        assert plan.plan_version == "v1"
        assert plan.global_constraints is not None
        assert plan.global_constraints.budget.stretch_maximum is not None
        assert plan.source.fast_parser_used is True


# ── 12. Merge LLM patch ─────────────────────────────────────────


class TestMergeLlmPatch:
    def test_valid_patch_merges(self):
        base = normalize_fast_parse_to_plan(_laptop_parse())
        patch = {"ambiguities": ["Bu model için garanti bilgisi eksik"]}
        merged = merge_llm_plan_patch(base, patch)
        assert "Bu model için garanti bilgisi eksik" in merged.ambiguities

    def test_invalid_patch_returns_base_with_clarification(self):
        base = normalize_fast_parse_to_plan(_laptop_parse())
        patch = {"plan_version": "v99", "items": []}
        merged = merge_llm_plan_patch(base, patch)
        assert merged.clarification_required is True


# ── 13. Executor ─────────────────────────────────────────────────


class TestExecutor:
    def test_hard_filter_eliminates(self):
        plan = CanonicalSearchPlan(
            items=[PlanItem(
                item_id="item-1",
                hard_constraints=[
                    PlanConstraint(
                        constraint_id="c-brand-1",
                        dimension="brand",
                        operator=ConstraintOperator.EQ,
                        value="BrandX",
                        strength=ConstraintStrength.HARD,
                    )
                ],
            )],
        )
        products = [
            {"product_id": "p1", "brand": "BrandX", "price": 1000},
            {"product_id": "p2", "brand": "BrandY", "price": 900},
        ]
        filtered = filter_products_by_plan(products, plan)
        assert len(filtered) == 1
        assert filtered[0]["product_id"] == "p1"

    def test_excluded_constraint_eliminates(self):
        plan = CanonicalSearchPlan(
            items=[PlanItem(
                item_id="item-1",
                excluded_constraints=[
                    PlanConstraint(
                        constraint_id="c-cat-1",
                        dimension="category",
                        operator=ConstraintOperator.EQ,
                        value="tablet",
                        strength=ConstraintStrength.HARD,
                    )
                ],
            )],
        )
        products = [
            {"product_id": "p1", "category": "laptop", "price": 1000},
            {"product_id": "p2", "category": "tablet", "price": 800},
        ]
        filtered = filter_products_by_plan(products, plan)
        assert len(filtered) == 1
        assert filtered[0]["product_id"] == "p1"

    def test_score_with_ranking_priorities(self):
        plan = CanonicalSearchPlan(
            items=[PlanItem(
                item_id="item-1",
                ranking_priorities=["ram"],
                soft_preferences=[
                    PlanConstraint(
                        constraint_id="c-ram-1",
                        dimension="ram",
                        operator=ConstraintOperator.GTE,
                        value=16,
                        strength=ConstraintStrength.SOFT,
                        confidence=0.9,
                    )
                ],
            )],
            global_constraints=GlobalConstraints(
                budget=BudgetConstraint(target_maximum=50000),
            ),
        )
        p_high = {"product_id": "p1", "ram": 32, "price": 25000}
        p_low = {"product_id": "p2", "ram": 8, "price": 20000}
        score_high = score_product_for_plan(p_high, plan)
        score_low = score_product_for_plan(p_low, plan)
        assert score_high > score_low


# ── 14. Clarification planner ───────────────────────────────────


class TestClarificationPlanner:
    def test_budget_stretch_question(self):
        plan = CanonicalSearchPlan(
            items=[PlanItem(item_id="item-1")],
            global_constraints=GlobalConstraints(
                budget=BudgetConstraint(target_maximum=30000, stretch_maximum=34500),
            ),
        )
        qs = build_clarification_questions(plan)
        assert len(qs) >= 1
        assert qs[0]["question_type"] == "budget_stretch"
        assert "30,000" in qs[0]["text"] or "30.000" in qs[0]["text"] or "30000" in qs[0]["text"]

    def test_multi_category_question(self):
        plan = normalize_fast_parse_to_plan(_multi_category_parse())
        qs = build_clarification_questions(plan)
        multi_qs = [q for q in qs if q["question_type"] == "multi_category"]
        assert len(multi_qs) == 1

    def test_max_questions_respected(self):
        plan = CanonicalSearchPlan(
            request_type=RequestType.MULTI_ITEM_BUNDLE,
            items=[
                PlanItem(item_id="i1", category=CategoryRef(resolved_id="c1", raw_text="Cat1")),
                PlanItem(item_id="i2", category=CategoryRef(resolved_id="c2", raw_text="Cat2")),
            ],
            global_constraints=GlobalConstraints(
                budget=BudgetConstraint(target_maximum=10000, stretch_maximum=12000),
            ),
            conflicts=[{"type": "HARD_EXCLUDED_SAME_DIMENSION", "dimension": "brand", "item_id": "i1"}],
        )
        qs = build_clarification_questions(plan, max_questions=1)
        assert len(qs) <= 1

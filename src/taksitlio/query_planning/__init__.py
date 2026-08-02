"""Complex query planning package."""

from taksitlio.query_planning.bundle import BundleResult, solve_bundle
from taksitlio.query_planning.capability_checker import (
    SUBJECTIVE_DIMENSIONS,
    check_capabilities,
)
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
    StateOperationRecord,
)
from taksitlio.query_planning.normalizer import normalize_fast_parse_to_plan
from taksitlio.query_planning.planner import (
    build_plan_from_fast_parse,
    detect_complex_route,
    merge_llm_plan_patch,
    plan_to_constraints_dict,
)
from taksitlio.query_planning.schema import CANONICAL_PLAN_SCHEMA, FORBIDDEN_PLAN_FIELDS
from taksitlio.query_planning.state_reducer import (
    StaleVersionError,
    apply_operation,
)
from taksitlio.query_planning.validator import PlanValidationError, validate_plan

__all__ = [
    "CANONICAL_PLAN_SCHEMA",
    "BudgetConstraint",
    "BundleResult",
    "CampaignIntent",
    "CanonicalSearchPlan",
    "CategoryRef",
    "ConditionalException",
    "ConstraintOperator",
    "ConstraintStrength",
    "FORBIDDEN_PLAN_FIELDS",
    "GlobalConstraints",
    "PLAN_VERSION",
    "PlanConstraint",
    "PlanItem",
    "PlanSource",
    "PlanValidationError",
    "RequestType",
    "SUBJECTIVE_DIMENSIONS",
    "StaleVersionError",
    "StateOperation",
    "StateOperationRecord",
    "apply_operation",
    "build_clarification_questions",
    "build_plan_from_fast_parse",
    "check_capabilities",
    "detect_complex_route",
    "filter_products_by_plan",
    "merge_llm_plan_patch",
    "normalize_fast_parse_to_plan",
    "plan_to_constraints_dict",
    "resolve_conflicts",
    "score_product_for_plan",
    "solve_bundle",
    "validate_plan",
]

# ADR-016: Canonical Search Plan Contract

## Status

Accepted (PROD-FINAL)

## Context

Search sessions previously used `FastParseResult` + `QueryNeedState` without a shared execution contract for retrieval, ranking, clarification, and UI chips.

## Decision

`CanonicalSearchPlan` is the typed contract consumed by:

- retrieval / hard filtering (`filter_products_by_plan`)
- progressive_results constraints bridge (`plan_to_constraints_dict`)
- clarification planner
- frontend chips (`chips_from_plan`)
- conversation state reducer operations

Request types: `SINGLE_PRODUCT_SEARCH`, `MULTI_ITEM_BUNDLE`, `PRODUCT_AND_CAMPAIGN_SEARCH`, `EXPLORATORY_SEARCH`, `COMPARISON`, `OUT_OF_SCOPE`.

Constraint strength: `HARD` | `SOFT` | `OPTIONAL` with generic operators (`EQ`, `NEQ`, `GT`, …).

Budget supports `target_maximum` and `stretch_maximum` with versioned conditional-exception thresholds (not LLM judgment).

## State semantics

Supported operations: `ADD`, `REMOVE`, `REPLACE`, `RELAX`, `REQUIRE`, `PREFER`, `TEMPORARY_EXCEPTION`, `ROLLBACK`, `CLEAR`.

Rules:

- stale query versions rejected
- removed/negated constraints cannot silently resurrect
- rollback restores prior snapshots within the same session
- pinned catalog revision unchanged by state ops

## Failure behavior

Unknown/unsupported catalog dimensions → `unsupported_dimensions` / honest UI chip. No invented attributes.

## Rollback

Reducer and plan fields are additive; API still accepts legacy `UPDATE`/`DELETE` aliases.

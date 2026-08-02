# ADR-015: Complex Query Planner

## Status

Accepted (PROD-FINAL)

## Context

Audit `CURRENT-SYSTEM-DEEP-AUDIT` showed basic INTERNAL product search works, but there was no first-class complex query plan. Partial substitutes (`required` flags, single `ranking_mode`, budget soft penalty) could not express hard/soft preference lists, conditional exceptions, multi-item bundles, or ranking priority sequences.

## Decision

Introduce `src/taksitlio/query_planning/` with a canonical plan model (`CanonicalSearchPlan`, plan_version `v1`) produced primarily from the existing deterministic `fast_parse`, optionally enriched by a schema-validated LLM patch.

Production path remains:

```text
POST /v1/search-sessions → SearchOrchestrator
```

The planner is hooked after state merge / before gap analysis and retrieval. It does **not** replace SearchOrchestrator, SSE, cohort pinning, or finance firewall.

## Alternatives considered

1. **Rewrite ChatOrchestrator as primary path** — rejected; ADR-011 search sessions is the production path.
2. **LLM-only planner emitting catalog IDs** — rejected; forbidden identifiers and source-backed retrieval are mandatory.
3. **Keep only FastParseResult** — rejected; cannot represent conditional exceptions / bundles / priority lists.

## LLM vs deterministic boundary

LLM may propose intents, constraint candidates, soft preferences, ambiguities, clarification suggestions, ranking priority suggestions.

LLM must never emit product/offer/merchant/price/stock/bank/campaign/agreement/rate/payment/URL identifiers. Validation rejects forbidden fields.

## Failure behavior

Malformed LLM output → one repair attempt → deterministic partial plan → clarification or safe no-result. Hard constraints are never auto-relaxed.

## Rollback

Feature can be neutralized by skipping plan merge in orchestrator (parse-only path remains). Migration V039 policies are additive.

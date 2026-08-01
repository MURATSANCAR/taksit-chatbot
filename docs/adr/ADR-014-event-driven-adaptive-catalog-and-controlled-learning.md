# ADR-014: Event-driven adaptive catalog, controlled learning, merchant readiness

**Status:** Accepted (Recovery P2-LIVE)  
**Date:** 2026-08-01

## Context

Recovery P1 proved production-ID retrieval, finance verification, and payment
persistence on an isolated snapshot. The live catalog continues to grow via Auto
Ops. Hardcoding merchants, banks, brands, categories, aliases, ranking weights,
or release thresholds in application code cannot scale and violates the
no-static-mapping rule.

Uncontrolled online self-training (promote alias from one typo) is unsafe.

## Decision

Adopt an **event-driven adaptive catalog and ranking system**:

1. Business names and thresholds live in DB / policy store / versioned config.
2. Production code knows only generic enums (`ENTITY_TYPE`, `EVENT_TYPE`,
   `QUALITY_STATUS`, learning lifecycle, …).
3. Learning lifecycle is mandatory:

   `OBSERVED → CANDIDATE → VALIDATED → SHADOW → PROMOTED`

   Records are never created as `PROMOTED`. Single-observation alias promotion
   is forbidden by policy thresholds.
4. Domain events drive **selective** projection refresh (affected product /
   merchant IDs only — never full-catalog recompute per price change).
5. Ranking adaptation uses champion/challenger with a deterministic safety
   floor; ML may reorder eligible candidates only.
6. Merchant readiness is recomputed from versioned policy thresholds; READY
   merchants can auto-degrade; search release scope is derived dynamically.
7. Media quality uses short/long edge rules from `media_quality_policies`
   (square images are not required).
8. Search sessions pin `catalog_revision`, `entity_index_revision`,
   `finance_revision`, `ranking_policy_version` for answer consistency.
9. LLM inferences are labeled `LLM_INFERENCE` and cannot auto-publish global
   learning.
10. Personal preferences (`USER_PREFERENCE_MEMORY`) never enter global learning
    unless anonymized and scoped as `GLOBAL_ENTITY_LEARNING`.

## Consequences

- Migration `V029__recovery_p2_live_adaptive_catalog.sql` adds learning,
  readiness, event, ranking projection, drift, and golden tables.
- Auto Ops may ingest, refresh projections, generate candidates, downgrade
  readiness, and run shadow evaluation — but must not create bank agreements,
  elevate campaign verification, change finance formulas, override alias
  conflicts, or swap ranking champions without gates.
- Do not describe the system as a “self-learning model”; report it as a
  controlled, versioned, event-driven adaptive system.

# ADR-017: Finance-Ready Cohort and Campaign Grounding

## Status

Accepted (PROD-FINAL) — Campaign Gate remains **CLOSED** until finance-ready gate passes.

## Context

Finance agreements and rate rows exist for merchants that are not search-ready. Active product-search cohort has `finance_ready_product_count = 0`. Showing campaign claims would invent fulfillment.

## Decision

1. Keep `finance_firewall` and default `finance_display=BLOCKED`.
2. Separate release channels (V039):
   - `internal_product_search` (INTERNAL traffic)
   - `public_canary_package` (package_state vs traffic_state; traffic stays `NOT_STARTED`)
3. Merchant expansion uses versioned `merchant_selection_policies` scores — **no hardcoded merchant branches**.
4. Finance capability may open to INTERNAL only when:
   - finance-ready products > 0 on the active search cohort
   - verified agreement + active campaign/rate
   - payment calculations deterministic
   - finance E2E + golden + zero leakage gates pass
5. Public finance / public traffic require separate human GO — never auto-enabled by this work.

## Grounding

Every finance claim must carry evidence IDs: product, offer, merchant, agreement, financial product, campaign, rate snapshot, calculation timestamp.

LLM never computes payments.

## Failure behavior

If campaign requested but finance not ready → capability unavailable / no finance fields (firewall). Product search may still return non-finance results.

## Rollback

Leave `finance_display` DISABLED/BLOCKED; do not mutate public `traffic_state`.

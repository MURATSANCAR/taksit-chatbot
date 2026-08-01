# V029 Production Rollout Plan

**Status:** `PLANNED`  
**Migration:** `db/migrations/V029__recovery_p2_live_adaptive_catalog.sql`  
**Companion:** `db/migrations/V030__p2_live_activation_flags_and_search_ready.sql`  
**Auto-apply:** **Forbidden** — requires explicit approval.

System definition: kontrollü, versioned, event-driven adaptif katalog ve ranking sistemi  
(not a self-learning model).

---

## Owner / Approval

| Role | Responsibility |
|---|---|
| Owner | Platform / catalog ops |
| Approver | Tech lead + DB owner |
| Executor | Nanobase operator via documented window |
| Status values | `PLANNED` → `APPROVED` → `RUNNING` → `VERIFIED` / `FAILED` / `ROLLED_BACK` |

Current status: **PLANNED** (dry-run on staging required before `APPROVED`).

---

## Pre-check

1. Confirm latest backup / PITR restore point for `taksitlio`.
2. Confirm staging dry-run artifact  
   `artifacts/e2e-production-verification/p2-live-activation/v029-dry-run.json` shows:
   - `product_loss=0`, `offer_loss=0`, `finance_option_loss=0`, `media_loss=0`
3. Confirm analysis risks acceptable  
   `v029-migration-analysis.json` — no HIGH `TABLE_REWRITE_RISK`.
4. Confirm Auto Ops can pause non-critical writers if needed (optional).
5. Confirm feature flags will remain:
   - `learning_auto_promotion_enabled=DISABLED`
   - `dynamic_readiness_enabled=SHADOW`
   - `adaptive_ranking_enabled=SHADOW`
6. Deployment window agreed (low traffic).

---

## Database backup / reference snapshot

```bash
# On nanobase — reference snapshot name (example)
ssh nanobase 'pg_dump -Fc "$DATABASE_URL" -f /var/backups/taksitlio/taksitlio_pre_v029_$(date -u +%Y%m%dT%H%M%SZ).dump'
```

Retain dump until `VERIFIED` + 7 days.

---

## Deployment window

- Prefer off-peak; expected DDL duration: seconds–low minutes on empty new tables.
- `merchants` CHECK widen and `search_sessions` ADD COLUMN are brief metadata locks.
- No full rewrite of `products` / `product_offers` / `media_assets`.

---

## Migration command

```bash
ssh nanobase 'cd /data/nanobaseai/taksitlio-chatbot && set -a && . ./.env.runtime && set +a
# Status → RUNNING after approval
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/V029__recovery_p2_live_adaptive_catalog.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/V030__p2_live_activation_flags_and_search_ready.sql
'
```

Idempotent: safe to re-run (`IF NOT EXISTS`).

---

## Health checks (post-migration)

```sql
-- Tables present
SELECT table_name FROM information_schema.tables
WHERE table_name IN (
  'catalog_domain_events','merchant_readiness_snapshots',
  'product_ranking_feature_projection','runtime_feature_flags',
  'search_ready_product_projection'
) ORDER BY 1;

-- Inventory unchanged
SELECT
  (SELECT count(*) FROM products WHERE status='ACTIVE') AS products,
  (SELECT count(*) FROM product_offers) AS offers,
  (SELECT count(*) FROM media_assets WHERE status='READY') AS media_ready,
  (SELECT count(*) FROM product_finance_options WHERE eligibility_status='ELIGIBLE') AS finance_opts;

-- Flags
SELECT flag_code, status FROM runtime_feature_flags ORDER BY 1;
```

Chatbot existing paths must continue (flags SHADOW/DISABLED for adaptive cutover).

---

## Post-migration verification

1. Inventory deltas = 0 vs pre-check.
2. Feature flags match seed (auto promotion DISABLED).
3. Run `scripts/auto_ops_learning_jobs.py` once — jobs ledger rows appear.
4. Run `scripts/run_p2_live_activation.py` read-only gates.
5. Mark rollout `VERIFIED` only if health checks pass.

---

## Rollback conditions

Rollback if any of:

- Product/offer/media/finance counts drop unexpectedly
- Application errors spike on catalog reads
- Migration leaves partial objects / failed transaction requiring cleanup
- Unexpected long locks > agreed limit

---

## Rollback command

```sql
BEGIN;
DROP TABLE IF EXISTS search_ready_product_projection CASCADE;
DROP TABLE IF EXISTS runtime_feature_flags CASCADE;
DROP TABLE IF EXISTS merchant_priority_policies CASCADE;
-- V029 learning/event tables (safe if empty / unused)
DROP TABLE IF EXISTS auto_ops_jobs CASCADE;
DROP TABLE IF EXISTS catalog_drift_alarms CASCADE;
DROP TABLE IF EXISTS continuous_golden_cases CASCADE;
DROP TABLE IF EXISTS continuous_golden_sets CASCADE;
DROP TABLE IF EXISTS search_release_scope CASCADE;
DROP TABLE IF EXISTS merchant_readiness_snapshots CASCADE;
DROP TABLE IF EXISTS merchant_readiness_policy_versions CASCADE;
DROP TABLE IF EXISTS merchant_readiness_policies CASCADE;
DROP TABLE IF EXISTS media_quality_learning_candidates CASCADE;
DROP TABLE IF EXISTS media_quality_policies CASCADE;
DROP TABLE IF EXISTS product_ranking_feature_projection CASCADE;
DROP TABLE IF EXISTS ranking_feedback_events CASCADE;
DROP TABLE IF EXISTS ranking_experiments CASCADE;
DROP TABLE IF EXISTS ranking_policy_versions CASCADE;
DROP TABLE IF EXISTS ranking_feature_definitions CASCADE;
DROP TABLE IF EXISTS alias_learning_versions CASCADE;
DROP TABLE IF EXISTS alias_learning_evidence CASCADE;
DROP TABLE IF EXISTS alias_learning_candidates CASCADE;
DROP TABLE IF EXISTS query_resolution_observations CASCADE;
DROP TABLE IF EXISTS attribute_extraction_versions CASCADE;
DROP TABLE IF EXISTS attribute_extraction_candidates CASCADE;
DROP TABLE IF EXISTS category_attribute_policies CASCADE;
DROP TABLE IF EXISTS attribute_aliases CASCADE;
DROP TABLE IF EXISTS attribute_units CASCADE;
DROP TABLE IF EXISTS attribute_definitions CASCADE;
DROP TABLE IF EXISTS brand_learning_candidates CASCADE;
DROP TABLE IF EXISTS taxonomy_mapping_evidence CASCADE;
DROP TABLE IF EXISTS taxonomy_mapping_versions CASCADE;
DROP TABLE IF EXISTS taxonomy_mapping_candidates CASCADE;
DROP TABLE IF EXISTS source_taxonomy_nodes CASCADE;
DROP TABLE IF EXISTS source_taxonomies CASCADE;
DROP TABLE IF EXISTS feed_processing_metrics CASCADE;
DROP TABLE IF EXISTS catalog_domain_events CASCADE;
DROP TABLE IF EXISTS learning_promotion_policies CASCADE;
ALTER TABLE search_sessions DROP COLUMN IF EXISTS catalog_revision;
ALTER TABLE search_sessions DROP COLUMN IF EXISTS entity_index_revision;
ALTER TABLE search_sessions DROP COLUMN IF EXISTS finance_revision;
ALTER TABLE search_sessions DROP COLUMN IF EXISTS ranking_policy_version;
-- Restore merchants CHECK to READY|PARTIAL|BLOCKED if required by older app builds
ALTER TABLE merchants DROP CONSTRAINT IF EXISTS merchants_activation_gate_check;
ALTER TABLE merchants ADD CONSTRAINT merchants_activation_gate_check
  CHECK (activation_gate IN ('READY','PARTIAL','BLOCKED'));
COMMIT;
```

Mark status `ROLLED_BACK`. Re-deploy app build that does not require V029 tables if needed.

---

## Feature flag activation order

1. Candidate generation `ENABLED`
2. Auto promotion `DISABLED` (until separate security decision)
3. Dynamic readiness `SHADOW` → `ENABLED` after ≥3 policy-READY merchants proven
4. Adaptive ranking `SHADOW` → `ENABLED` after full-path P95 &lt; 50 ms + regression PASS
5. Rolling golden `ENABLED` (candidates only; APPROVED requires human `reviewed_by`)

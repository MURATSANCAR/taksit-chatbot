# Operator runbook — ADR-012 answer integrity (migrate + smoke)

**Goal:** Apply V023/V024 on a live Postgres, then verify breaker persist,
sponsored isolation, and feedback/shadow admin paths.

**Preconditions**
- Docker + Compose available (same stack as ADR-009), **or** an existing
  Postgres with `DATABASE_URL` set
- API can reach that database (`ALLOW_IN_MEMORY=false` for prod path)
- Campaign Gate remains **CLOSED** (no personalized credit claims)

Migrations:
- `db/migrations/V023__answer_integrity_claim_grounding.sql`
- `db/migrations/V024__sponsored_placements.sql`

## 1. Start Postgres (if needed)

```bash
cp .env.example .env.runtime && chmod 600 .env.runtime
# Ensure DATABASE_URL / POSTGRES_* are set in .env.runtime

docker compose --env-file .env.runtime \
  -f docker/docker-compose.runtime.yml \
  --profile runtime-verification up -d postgres
```

Fresh volume mounts `db/migrations` into `docker-entrypoint-initdb.d` — first
boot applies all `V*.sql`. **Existing** volumes do **not** re-run init scripts;
use the migrate module below.

## 2. Apply migrations (idempotent)

```bash
set -a && source .env.runtime && set +a
export PYTHONPATH=src
python -m taksitlio.db.migrate
```

Expect `apply` or `skip` for V023 and V024, then `done — newly applied=…`.

Verify tables:

```bash
psql "$DATABASE_URL" -c "\dt source_precedence_policies"
psql "$DATABASE_URL" -c "\dt quality_circuit_breakers"
psql "$DATABASE_URL" -c "\dt sponsored_placements"
psql "$DATABASE_URL" -c "\dt feedback_result_snapshots"
```

## 3. Smoke — circuit breaker from ingestion dry-run

Run admin dry-run (or dry-run-persist) against a feed that trips price quality
thresholds. Response should include `circuit_breaker.persisted=true` when the
store is wired.

```bash
curl -sS -X POST "$API/v1/admin/ingestion/dry-run" \
  -H 'content-type: application/json' \
  -d '{
    "source_code": "src-smoke",
    "adapter_code": "generic.json_feed.v1",
    "merchant_id": "<MERCHANT_ID>",
    "config": {"feed_path": "path/to/fixture.json"},
    "limit": 50
  }'
```

List open breakers:

```bash
curl -sS "$API/v1/admin/answer-integrity/circuit-breakers"
```

Product search for that merchant must omit price-led ranking when
`DISABLE_PRICE_RESULTS` is open (`price_disabled_merchant_ids` on the search
request).

## 4. Smoke — sponsored placement isolation

```bash
curl -sS -X PUT "$API/v1/admin/answer-integrity/sponsored" \
  -H 'content-type: application/json' \
  -d '{
    "product_id": "<PRODUCT_ID>",
    "weight": 100,
    "merchant_id": "<MERCHANT_ID>",
    "active": true,
    "label": "sponsored"
  }'

curl -sS "$API/v1/admin/answer-integrity/sponsored"
```

Search must keep sponsored items labeled and must not let sponsored weight
steal organic “en uygun” / best-offer identity.

Deactivate:

```bash
curl -sS -X DELETE "$API/v1/admin/answer-integrity/sponsored/<PRODUCT_ID>"
```

## 5. Smoke — feedback / shadow / error-class

```bash
curl -sS -X POST "$API/v1/admin/answer-integrity/feedback" \
  -H 'content-type: application/json' \
  -d '{
    "query_version": 1,
    "parsed_constraints": {},
    "selected_product": null,
    "error_class": "LLM_EXPLANATION_ERROR",
    "user_note": "ops smoke"
  }'

curl -sS -X POST "$API/v1/admin/answer-integrity/shadow-compare" \
  -H 'content-type: application/json' \
  -d '{
    "live": {"text": "template reply", "facts": []},
    "shadow": {"text": "llm draft", "facts": []}
  }'

curl -sS -X POST "$API/v1/admin/answer-integrity/error-class" \
  -H 'content-type: application/json' \
  -d '{
    "error_class": "LLM_EXPLANATION_ERROR",
    "owner": "ops-smoke",
    "metric_key": "adr012.smoke",
    "detail": "runbook smoke"
  }'
```

With Postgres DI, rows land in V023 tables. Demo/`ALLOW_IN_MEMORY` uses
in-memory stores only.

## 6. Unit gates (no live DB)

```bash
export PYTHONPATH=src
python -m pytest \
  tests/unit/answer_integrity \
  tests/acceptance/answer_integrity \
  -q
```

## Done when

- [ ] V023 + V024 applied (or skipped as already present)
- [ ] Dry-run → breaker visible via admin list
- [ ] Sponsored CRUD → search isolation holds
- [ ] Feedback / shadow / error-class write without 5xx
- [ ] Unit/acceptance ADR-012 suites green

## Out of scope here

- ADR-009 live FAST HR100 / provisional accept
- ADR-010 live merchant credentials / public CDN DNS
- Replacing evidence proxies (`offer:{id}`) with raw snapshot PK plumbing —
  cards already carry `price_snapshot_id` when the catalog supplies it

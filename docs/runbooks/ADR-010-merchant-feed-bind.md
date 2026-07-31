# Operator runbook — bind first real merchant feed (ADR-010 P15)

**Goal:** Connect a live JSON product feed without putting merchant/bank names
or secrets in application code.

**Preconditions**
- API running with Postgres (or `ALLOW_IN_MEMORY=true` for dry rehearsal)
- Feed matches `generic.json_feed.v1` shape (`{"products":[...]}`)
- Campaign Gate remains **CLOSED** (no personalized credit approval)

## 1. Put feed token in the environment

```bash
export MERCHANT_FEED_TOKEN='…'   # never commit; never paste into API JSON
```

Supported `credential_ref` forms:

| Ref | Effect |
|---|---|
| `env://MERCHANT_FEED_TOKEN` | `Authorization: Bearer <value>` |
| `bearer:env://MERCHANT_FEED_TOKEN` | same, explicit |
| `header:X-Api-Key:env://MERCHANT_FEED_TOKEN` | custom header |

Inline `authorization` / `api_key` in source config is **rejected**.

## 2. Create opaque merchant row

Display name comes from ops input into DB — not from app hardcoding.

```bash
curl -sS -X POST "$API/v1/admin/merchants" \
  -H 'content-type: application/json' \
  -d '{"merchant_code":"ops-m-001","display_name":"<ops-provided label>"}'
```

Note returned `id` → `MERCHANT_ID`.

## 3. Dry-run (no catalog write)

```bash
curl -sS -X POST "$API/v1/admin/ingestion/dry-run" \
  -H 'content-type: application/json' \
  -d "{
    \"source_code\": \"src-m-001\",
    \"adapter_code\": \"generic.json_feed.v1\",
    \"merchant_id\": \"$MERCHANT_ID\",
    \"credential_ref\": \"env://MERCHANT_FEED_TOKEN\",
    \"config\": {\"feed_url\": \"https://feeds.example/products.json\"},
    \"limit\": 20
  }"
```

Inspect `chatbot_visible`, `quarantined`, and quality reasons. Fix feed/mapping
before persist.

Local file rehearsal (no network / no token):

```json
"config": {"feed_path": "/path/to/fixture.json"}
```

## 4. Persist + upsert products

```bash
curl -sS -X POST "$API/v1/admin/ingestion/dry-run/persist" \
  -H 'content-type: application/json' \
  -d "{
    \"source_code\": \"src-m-001\",
    \"adapter_code\": \"generic.json_feed.v1\",
    \"merchant_id\": \"$MERCHANT_ID\",
    \"merchant_id_int\": $MERCHANT_ID,
    \"credential_ref\": \"env://MERCHANT_FEED_TOKEN\",
    \"config\": {\"feed_url\": \"https://feeds.example/products.json\"},
    \"persist\": true,
    \"upsert_products\": true,
    \"enqueue_discovery\": false,
    \"limit\": 50
  }"
```

Media hotlinks are **not** shown in chat; scheduler `MEDIA_FETCH` copies to
object storage + CDN (`OBJECT_STORAGE_BACKEND`, `CDN_BASE_URL`).

## 5. Verify chatbot path

```bash
curl -sS -X POST "$API/v1/chat" \
  -H 'content-type: application/json' \
  -d '{"session_id":"ops-check","message":"<product need>","product_phase":"FINANCE_ENRICHED"}'
```

Expect `diagnostics.product_path: true` and CDN thumbnails when media is ready.
Guest UI: `/taksitlio/`.

## Guardrails

- No static typo maps for merchant/bank names
- No production fake seed of prices/rates
- Finance options require separate rebuild (`/v1/admin/finance-options/rebuild`)
  from real rate snapshots — never invent rates
- Personalized approval stays closed until ADR-009 Campaign Gate

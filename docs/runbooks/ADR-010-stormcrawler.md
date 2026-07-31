# ADR-010 — StormCrawler → JSON feed → ingestion

**Goal:** Background crawl (≥20 merchants + TR banks) produces JSON feeds;
Python ingestion reads feeds. Chatbot never crawls synchronously.

## Stack

```bash
docker compose -f docker/docker-compose.crawler.yml up -d
```

| Service | URL |
|---|---|
| Storm UI | http://127.0.0.1:8080 |
| URLFrontier | 127.0.0.1:7071 |
| Feed HTTP | http://127.0.0.1:8091/feeds/ |

Build topology JAR:

```bash
docker compose -f docker/docker-compose.crawler.yml --profile build run --rm topology-builder
```

Submit (ops; adjust nimbus host):

```bash
docker compose -f docker/docker-compose.crawler.yml exec nimbus \
  storm jar /topology/taksitlio-crawler.jar org.apache.storm.flux.Flux \
  --remote /topology/conf/crawler.flux
```

(Mount the built jar into nimbus/supervisor as needed.)

## Ops registry

[`crawler/ops/crawl-registry.yaml`](../../crawler/ops/crawl-registry.yaml) holds
merchant/bank codes, display names, seed URLs. **No** Python branching on
display names.

Coverage + seed plan:

```bash
python scripts/bind_crawl_feeds.py --coverage
python scripts/inject_crawl_seeds.py
# → crawler/ops/seed-plan.json
```

Inject seeds into URLFrontier using your frontier client, attaching metadata
keys from the seed plan (`taksitlio.source_code`, `taksitlio.channel`,
`taksitlio.institution_code` / `taksitlio.merchant_code`).

## Feed contract

- Product: `{"products":[...]}` → `generic.json_feed.v1`  
  (same as [`ADR-010-merchant-feed-bind.md`](ADR-010-merchant-feed-bind.md))
- Campaign: `{"campaigns":[...]}` → `generic.campaign_feed.v1`  
  Missing rates → **no invent**; campaign stays DRAFT/UNVERIFIED; rate rows
  only when `rate_apr` / fee explicitly present.

Indexer output path inside crawler network: `/feeds/{source_code}.json`  
HTTP: `http://127.0.0.1:8091/feeds/{source_code}.json`

## Local E2E without live crawl (fixtures)

```bash
python scripts/bind_crawl_feeds.py --fixtures --dry-run
```

Uses:

- `crawler/feeds/fixtures/src-m-teknosa.json`
- `crawler/feeds/fixtures/src-b-fibabanka.json`

## Bind to API

```bash
# App stack must be up (docker/docker-compose.yml)
python scripts/bind_crawl_feeds.py \
  --api http://127.0.0.1:8000 \
  --bind-merchants \
  --fixtures \
  --limit 1
```

Live feeds (after crawler wrote files):

```bash
python scripts/bind_crawl_feeds.py \
  --api http://127.0.0.1:8000 \
  --bind-merchants \
  --feed-base http://127.0.0.1:8091 \
  --limit 1
```

## Guardrails

- Honor robots.txt (`http.protocol.robots: true`); polite delay in
  `crawler/conf/crawler-conf.yaml`
- No fake prices/stock/rates
- Personalized Campaign Gate remains **CLOSED** (ADR-009)
- JS-only sites without JSON-LD/sitemap → low coverage / later BROWSER adapter

## Health checks

1. Storm UI shows topology workers
2. `curl -sS http://127.0.0.1:8091/health`
3. `curl -sS http://127.0.0.1:8091/feeds/` lists feed files after crawl/flush
4. Admin: `GET /v1/admin/ingestion/adapters` includes
   `generic.json_feed.v1` and `generic.campaign_feed.v1`

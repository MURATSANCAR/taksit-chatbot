# Taksitlio StormCrawler (ADR-010)

Java topology that crawls merchant/bank seed URLs and writes ADR-010 JSON feeds
under `/feeds/{source_code}.json`.

## Build

```bash
# from repo root
docker compose -f docker/docker-compose.crawler.yml --profile build run --rm topology-builder
```

Or locally (JDK 17 + Maven):

```bash
cd crawler && mvn -DskipTests package
```

## Config

- `conf/crawler.flux` — topology
- `conf/crawler-conf.yaml` — politeness, URLFrontier, feed dir
- `conf/parsefilters.json` — JSON-LD extract
- `ops/crawl-registry.yaml` — ≥20 merchants + banks (ops layer)

See [`docs/runbooks/ADR-010-stormcrawler.md`](../docs/runbooks/ADR-010-stormcrawler.md).

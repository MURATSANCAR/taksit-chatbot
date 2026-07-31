# Live merchant/bank feeds (ops)

Generated on the **server**, not committed to Git.

| Path | Role |
|---|---|
| `LIVE_FEED_DIR` (default `crawler/feeds/live`) | Staging: feed JSON, download cache, manifests |
| `MEDIA_STORAGE_ROOT` or S3 (`OBJECT_STORAGE_BACKEND`) | Canonical product/campaign images → CDN |
| Postgres `media_assets` / `product_media_links` | Metadata + CDN keys (no image blobs in git) |

See `docs/runbooks/ADR-010-s3-cdn-origin.md`.

## Scripts

```bash
# Fetch feeds + optional image download into LIVE_FEED_DIR
python scripts/fetch_live_merchant_feeds.py --merchant all --delay 2
python scripts/fetch_public_campaigns.py

# Apply catalog (products/offers) from live JSON
python scripts/ingest_live_feeds.py

# Push staged product images into object storage + DB (no hotlink)
python scripts/attach_local_product_media.py
```

Env (see `.env.example`):

```bash
LIVE_FEED_DIR=/var/lib/taksitlio/feeds/live   # optional override
MEDIA_STORAGE_ROOT=/data/media                # local backend
CDN_BASE_URL=https://cdn.your-domain.example
# or OBJECT_STORAGE_BACKEND=s3 + S3_* …
```

## Blockers seen

- **Teknosa**: Cloudflare bot challenge — needs residential/browser or official feed/API.
- **Bank APR tables**: only ingest when source explicitly publishes rate; otherwise campaign stays without rate snapshots.

Test/demo JSON lives under `crawler/feeds/fixtures/` (tracked). Live captures stay on disk/object storage only.

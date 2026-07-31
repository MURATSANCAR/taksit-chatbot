# Engelli merchant crawl (Koçtaş / D&R / Teknosa)

## Ne değişti?

| Merchant | Eski durum | Çözüm |
|----------|------------|--------|
| **D&R** | Kategori HTML’de PDP linki yok → 0 ürün | Resmi `sitemaps/products.xml` + JSON-LD PDP |
| **Koçtaş** | Eski kategori URL + `.html` regex; PDP Akamai **403** | Playwright ile homepage kartları; tam katalog için feed/proxy |
| **Teknosa** | Cloudflare **403** | Playwright / [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) |

## Komutlar

```bash
# D&R — sitemap, limit yok
python scripts/fetch_live_merchant_feeds.py --merchants dr --delay 0.35 --limit 0

# Koçtaş — Playwright (homepage partial)
python scripts/fetch_live_merchant_feeds.py --merchants koctas --limit 0

# Teknosa — browser + opsiyonel FlareSolverr
docker compose -f docker/docker-compose.crawler.yml --profile waf up -d flaresolverr
export FLARESOLVERR_URL=http://127.0.0.1:8191
pip install playwright && playwright install chromium
python scripts/fetch_live_merchant_feeds.py --merchants teknosa --delay 1 --limit 50
```

## Tam katalog için ops seçenekleri

1. **Merchant partner feed / Mirakl API** (ADR-010 tercih) — credential_ref ile `generic.json_feed.v1`
2. **FlareSolverr** sidecar (`docker-compose.crawler.yml` profile `waf`)
3. **Apify** Koçtaş actor (`caulleonard/koctas-product-scraper`) — `APIFY_TOKEN` ile ops job (üçüncü parti; resmi feed değil)

Akamai/Cloudflare bypass garantisi yok; IP/reputation’a bağlıdır. Sahte fiyat basılmaz.

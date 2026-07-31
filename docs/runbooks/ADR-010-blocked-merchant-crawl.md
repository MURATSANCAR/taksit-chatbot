# Engelli merchant crawl (Koçtaş / D&R / Teknosa)

## Vendor repos (kullanılan)

| Repo | Path | Rol |
|------|------|-----|
| [unclecode/crawl4ai](https://github.com/unclecode/crawl4ai) | `.venv-crawl` + `scripts/fetch_via_crawl4ai.py` | Cloudflare’i aşan browser crawl (Teknosa açıldı) |
| [FlareSolverr/FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) | `vendor/FlareSolverr` + compose profile `waf` | Opsiyonel CF challenge solver |
| [BerkayKOCAK/e-commerce-crawler](https://github.com/BerkayKOCAK/e-commerce-crawler) | `vendor/e-commerce-crawler` | TR merchant selector / sitemap ipuçları |

`vendor/` ve `.venv-crawl/` gitignore’da; clone + venv lokal/ops.

## Çalıştırma

```bash
python3.12 -m venv .venv-crawl
.venv-crawl/bin/pip install 'crawl4ai>=0.5' playwright httpx beautifulsoup4
.venv-crawl/bin/playwright install chromium

# Teknosa (önceden Cloudflare 403 — crawl4ai ile açıldı)
.venv-crawl/bin/python scripts/fetch_via_crawl4ai.py --merchants teknosa --delay 0.45 --limit 0

# Koçtaş homepage (PDP/category hâlâ Akamai 403)
.venv-crawl/bin/python scripts/fetch_via_crawl4ai.py --merchants koctas --limit 0
# veya Playwright DOM extract:
python scripts/fetch_live_merchant_feeds.py --merchants koctas --limit 0

# D&R resmi sitemap
python scripts/fetch_live_merchant_feeds.py --merchants dr --delay 0.35 --limit 0
```

## Sonuç (bu oturum)

- **Teknosa:** ~7k+ ürün + görsel URL (crawl4ai)
- **Koçtaş:** ~100+ homepage kartı (PDP Akamai kapalı)
- **D&R:** sitemap + JSON-LD

## Tam Koçtaş katalog

PDP’ler IP bazlı Akamai Access Denied. Kalan yollar: partner Mirakl feed, residential proxy + FlareSolverr, veya Apify `caulleonard/koctas-product-scraper`.

-- V016: ADR-010 P1 — products, canonical products, offers, price/stock snapshots
-- No demo/seed rows. Production data comes only via verified ingestion sources.

-- ---------------------------------------------------------------------------
-- Brands (dynamic; no hardcoded brand maps in application code)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS brands (
    id              BIGSERIAL PRIMARY KEY,
    brand_code      VARCHAR(64)  NOT NULL UNIQUE,
    display_name    VARCHAR(256) NOT NULL,
    normalized_name VARCHAR(256) NOT NULL,
    status          VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE', 'INACTIVE')),
    metadata        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_brands_normalized_name
    ON brands (normalized_name)
    WHERE status = 'ACTIVE';

CREATE TABLE IF NOT EXISTS brand_aliases (
    id               BIGSERIAL PRIMARY KEY,
    brand_id         BIGINT       NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    alias_text       VARCHAR(256) NOT NULL,
    normalized_alias VARCHAR(256) NOT NULL,
    locale           VARCHAR(16)  NOT NULL DEFAULT 'tr-TR',
    status           VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                     CHECK (status IN ('ACTIVE', 'INACTIVE')),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (normalized_alias, locale)
);

-- ---------------------------------------------------------------------------
-- Canonical products (safe merge only; low confidence stays separate)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS canonical_products (
    id                   BIGSERIAL PRIMARY KEY,
    canonical_code       VARCHAR(128) NOT NULL UNIQUE,
    display_name         VARCHAR(512) NOT NULL,
    brand_id             BIGINT       REFERENCES brands(id),
    model_number         VARCHAR(256),
    gtin                 VARCHAR(32),
    ean                  VARCHAR(32),
    mpn                  VARCHAR(128),
    match_confidence     NUMERIC(5,4),
    match_method         VARCHAR(64)
                         CHECK (match_method IS NULL OR match_method IN (
                             'GTIN', 'EAN', 'MPN', 'BRAND_MODEL', 'SIGNATURE', 'MANUAL'
                         )),
    status               VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                         CHECK (status IN ('ACTIVE', 'MERGED', 'QUARANTINED', 'INACTIVE')),
    metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_canonical_products_gtin
    ON canonical_products (gtin)
    WHERE gtin IS NOT NULL AND status = 'ACTIVE';

CREATE UNIQUE INDEX IF NOT EXISTS idx_canonical_products_ean
    ON canonical_products (ean)
    WHERE ean IS NOT NULL AND status = 'ACTIVE';

-- ---------------------------------------------------------------------------
-- Merchant products
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS products (
    id                   BIGSERIAL PRIMARY KEY,
    canonical_product_id BIGINT       REFERENCES canonical_products(id),
    merchant_id          BIGINT       NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    external_product_id  VARCHAR(256) NOT NULL,
    merchant_sku         VARCHAR(256),
    gtin                 VARCHAR(32),
    ean                  VARCHAR(32),
    mpn                  VARCHAR(128),
    brand_id             BIGINT       REFERENCES brands(id),
    model_number         VARCHAR(256),
    display_name         VARCHAR(512) NOT NULL,
    normalized_name      VARCHAR(512),
    short_description    TEXT,
    full_description     TEXT,
    manufacturer_name    VARCHAR(256),
    condition            VARCHAR(32)  NOT NULL DEFAULT 'NEW'
                         CHECK (condition IN ('NEW', 'REFURBISHED', 'USED', 'UNKNOWN')),
    warranty_summary     TEXT,
    status               VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                         CHECK (status IN (
                             'ACTIVE', 'UNAVAILABLE', 'QUARANTINED', 'REJECTED', 'DRAFT'
                         )),
    data_quality_status  VARCHAR(32)  NOT NULL DEFAULT 'PARTIAL'
                         CHECK (data_quality_status IN (
                             'READY', 'PARTIAL', 'QUARANTINED', 'REJECTED'
                         )),
    source_url           TEXT,
    source_updated_at    TIMESTAMPTZ,
    content_hash         VARCHAR(128),
    source_reference     TEXT,
    first_seen_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_seen_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_verified_at     TIMESTAMPTZ,
    attributes           JSONB        NOT NULL DEFAULT '{}'::jsonb,
    metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (merchant_id, external_product_id)
);

CREATE INDEX IF NOT EXISTS idx_products_merchant_status
    ON products (merchant_id, status);

CREATE INDEX IF NOT EXISTS idx_products_canonical
    ON products (canonical_product_id)
    WHERE canonical_product_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_products_normalized_name
    ON products (normalized_name)
    WHERE status = 'ACTIVE';

-- ---------------------------------------------------------------------------
-- Offers + history (never overwrite price without snapshot)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS product_offers (
    id                   BIGSERIAL PRIMARY KEY,
    product_id           BIGINT       NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    merchant_id          BIGINT       NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    location_id          BIGINT       REFERENCES merchant_locations(id) ON DELETE SET NULL,
    current_price        NUMERIC(14,2) NOT NULL,
    list_price           NUMERIC(14,2),
    currency             VARCHAR(3)   NOT NULL DEFAULT 'TRY',
    stock_status         VARCHAR(32)  NOT NULL DEFAULT 'UNKNOWN'
                         CHECK (stock_status IN (
                             'AVAILABLE', 'LIMITED', 'OUT_OF_STOCK', 'UNKNOWN'
                         )),
    shipping_cost        NUMERIC(14,2),
    seller_name          VARCHAR(256),
    checkout_url         TEXT,
    freshness_status     VARCHAR(32)  NOT NULL DEFAULT 'UNVERIFIED'
                         CHECK (freshness_status IN (
                             'FRESH', 'STALE', 'EXPIRED', 'UNVERIFIED', 'SOURCE_UNAVAILABLE'
                         )),
    valid_from           TIMESTAMPTZ,
    valid_until          TIMESTAMPTZ,
    content_hash         VARCHAR(128),
    source_reference     TEXT,
    last_verified_at     TIMESTAMPTZ,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_product_offers_product
    ON product_offers (product_id);

CREATE INDEX IF NOT EXISTS idx_product_offers_merchant_fresh
    ON product_offers (merchant_id, freshness_status)
    WHERE freshness_status = 'FRESH';

CREATE TABLE IF NOT EXISTS product_offer_snapshots (
    id               BIGSERIAL PRIMARY KEY,
    offer_id         BIGINT       NOT NULL REFERENCES product_offers(id) ON DELETE CASCADE,
    price            NUMERIC(14,2) NOT NULL,
    list_price       NUMERIC(14,2),
    stock_status     VARCHAR(32)  NOT NULL,
    currency         VARCHAR(3)   NOT NULL DEFAULT 'TRY',
    content_hash     VARCHAR(128),
    source_reference TEXT,
    captured_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_product_offer_snapshots_offer_captured
    ON product_offer_snapshots (offer_id, captured_at DESC);

CREATE TABLE IF NOT EXISTS product_price_history (
    id               BIGSERIAL PRIMARY KEY,
    offer_id         BIGINT       NOT NULL REFERENCES product_offers(id) ON DELETE CASCADE,
    price            NUMERIC(14,2) NOT NULL,
    list_price       NUMERIC(14,2),
    currency         VARCHAR(3)   NOT NULL DEFAULT 'TRY',
    content_hash     VARCHAR(128),
    source_reference TEXT,
    captured_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_product_price_history_offer_captured
    ON product_price_history (offer_id, captured_at DESC);

CREATE TABLE IF NOT EXISTS product_stock_snapshots (
    id               BIGSERIAL PRIMARY KEY,
    offer_id         BIGINT       NOT NULL REFERENCES product_offers(id) ON DELETE CASCADE,
    stock_status     VARCHAR(32)  NOT NULL,
    quantity         INTEGER,
    content_hash     VARCHAR(128),
    source_reference TEXT,
    captured_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_product_stock_snapshots_offer_captured
    ON product_stock_snapshots (offer_id, captured_at DESC);

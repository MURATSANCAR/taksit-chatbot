-- V022: ADR-011 P2 — merchant/brand media links for logo CDN (crawl/media pipeline).
-- Mirrors financial_institution_media; no seed logos. CDN only via media_assets.

CREATE TABLE IF NOT EXISTS merchant_media (
    id               BIGSERIAL PRIMARY KEY,
    merchant_id      BIGINT       NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    media_asset_id   BIGINT       NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    role             VARCHAR(32)  NOT NULL DEFAULT 'LOGO'
                     CHECK (role IN ('PRIMARY', 'LOGO', 'ICON')),
    is_primary       BOOLEAN      NOT NULL DEFAULT FALSE,
    valid_from       TIMESTAMPTZ,
    valid_until      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (merchant_id, media_asset_id, role)
);

CREATE INDEX IF NOT EXISTS idx_merchant_media_primary
    ON merchant_media (merchant_id)
    WHERE is_primary = TRUE;

CREATE TABLE IF NOT EXISTS brand_media (
    id               BIGSERIAL PRIMARY KEY,
    brand_id         BIGINT       NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    media_asset_id   BIGINT       NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    role             VARCHAR(32)  NOT NULL DEFAULT 'LOGO'
                     CHECK (role IN ('PRIMARY', 'LOGO', 'ICON')),
    is_primary       BOOLEAN      NOT NULL DEFAULT FALSE,
    valid_from       TIMESTAMPTZ,
    valid_until      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (brand_id, media_asset_id, role)
);

CREATE INDEX IF NOT EXISTS idx_brand_media_primary
    ON brand_media (brand_id)
    WHERE is_primary = TRUE;

-- V017: ADR-010 P2 — media assets, product links, variants, quality
-- Hotlink forbidden: chatbot serves CDN/object-storage URLs only.

CREATE TABLE IF NOT EXISTS media_assets (
    id                  BIGSERIAL PRIMARY KEY,
    source_url          TEXT         NOT NULL,
    storage_key         TEXT,
    cdn_url             TEXT,
    original_filename   VARCHAR(512),
    mime_type           VARCHAR(128),
    width               INTEGER,
    height              INTEGER,
    file_size           BIGINT,
    sha256              VARCHAR(64)  NOT NULL,
    perceptual_hash     VARCHAR(64),
    quality_score       NUMERIC(8,4),
    background_score    NUMERIC(8,4),
    watermark_score     NUMERIC(8,4),
    status              VARCHAR(32)  NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN (
                            'PENDING', 'READY', 'QUARANTINED', 'REJECTED',
                            'IMAGE_UNAVAILABLE', 'FAILED'
                        )),
    source_reference    TEXT,
    first_seen_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_verified_at    TIMESTAMPTZ,
    metadata            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_media_assets_sha256
    ON media_assets (sha256);

CREATE INDEX IF NOT EXISTS idx_media_assets_perceptual_hash
    ON media_assets (perceptual_hash)
    WHERE perceptual_hash IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_media_assets_status
    ON media_assets (status);

CREATE TABLE IF NOT EXISTS product_media_links (
    id                  BIGSERIAL PRIMARY KEY,
    product_id          BIGINT       NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    media_asset_id      BIGINT       NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    media_role          VARCHAR(32)  NOT NULL DEFAULT 'GALLERY'
                        CHECK (media_role IN (
                            'PRIMARY', 'GALLERY', 'THUMBNAIL',
                            'PACKAGING', 'DETAIL', 'COLOR_VARIANT'
                        )),
    display_order       INTEGER      NOT NULL DEFAULT 0,
    is_primary          BOOLEAN      NOT NULL DEFAULT FALSE,
    source_priority     INTEGER      NOT NULL DEFAULT 100,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (product_id, media_asset_id, media_role)
);

CREATE INDEX IF NOT EXISTS idx_product_media_links_product_primary
    ON product_media_links (product_id)
    WHERE is_primary = TRUE;

CREATE TABLE IF NOT EXISTS media_variants (
    id                  BIGSERIAL PRIMARY KEY,
    media_asset_id      BIGINT       NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    variant_code        VARCHAR(64)  NOT NULL,
    width               INTEGER      NOT NULL,
    height              INTEGER,
    mime_type           VARCHAR(128) NOT NULL DEFAULT 'image/webp',
    storage_key         TEXT         NOT NULL,
    cdn_url             TEXT,
    file_size           BIGINT,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (media_asset_id, variant_code)
);

CREATE INDEX IF NOT EXISTS idx_media_variants_asset
    ON media_variants (media_asset_id);

CREATE TABLE IF NOT EXISTS media_ingestion_runs (
    id                  BIGSERIAL PRIMARY KEY,
    product_id          BIGINT       REFERENCES products(id) ON DELETE SET NULL,
    source_url          TEXT         NOT NULL,
    status              VARCHAR(32)  NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN (
                            'PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'SKIPPED_DUPLICATE'
                        )),
    media_asset_id      BIGINT       REFERENCES media_assets(id) ON DELETE SET NULL,
    error_code          VARCHAR(64),
    error_detail        TEXT,
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_media_ingestion_runs_product
    ON media_ingestion_runs (product_id, created_at DESC);

CREATE TABLE IF NOT EXISTS media_quality_results (
    id                  BIGSERIAL PRIMARY KEY,
    media_asset_id      BIGINT       NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    min_width_ok        BOOLEAN      NOT NULL DEFAULT FALSE,
    min_height_ok       BOOLEAN      NOT NULL DEFAULT FALSE,
    preferred_width_ok  BOOLEAN      NOT NULL DEFAULT FALSE,
    aspect_ratio_ok     BOOLEAN      NOT NULL DEFAULT FALSE,
    decode_ok           BOOLEAN      NOT NULL DEFAULT FALSE,
    blur_acceptable     BOOLEAN      NOT NULL DEFAULT TRUE,
    product_coverage_ok BOOLEAN      NOT NULL DEFAULT TRUE,
    quality_score       NUMERIC(8,4),
    policy_version      VARCHAR(64)  NOT NULL DEFAULT 'media-quality-v1',
    evaluated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    detail              JSONB        NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_media_quality_results_asset
    ON media_quality_results (media_asset_id, evaluated_at DESC);

-- V027: ADR-010 / TASK-011 first delivery — searchable catalog projections.
-- Source of truth remains products / product_offers / brands / merchants / categories.
-- These tables are fully rebuildable; never write back into source catalog rows.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------------
-- product_search_projection (denormalized read model for fast retrieval)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS product_search_projection (
    product_id              BIGINT       PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    offer_id                BIGINT       REFERENCES product_offers(id) ON DELETE SET NULL,
    merchant_id             BIGINT       NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    merchant_name           VARCHAR(256) NOT NULL,
    merchant_alias_document TEXT         NOT NULL DEFAULT '',
    brand_id                BIGINT       REFERENCES brands(id) ON DELETE SET NULL,
    brand_name              VARCHAR(256),
    category_id             BIGINT       REFERENCES categories(id) ON DELETE SET NULL,
    category_path           TEXT,
    product_name            VARCHAR(512) NOT NULL,
    normalized_product_name VARCHAR(512),
    search_document         TEXT         NOT NULL DEFAULT '',
    current_price           NUMERIC(14,2),
    list_price              NUMERIC(14,2),
    currency                VARCHAR(3)   NOT NULL DEFAULT 'TRY',
    stock_status            VARCHAR(32)  NOT NULL DEFAULT 'UNKNOWN',
    primary_image_url       TEXT,
    product_url             TEXT,
    attribute_document      TEXT         NOT NULL DEFAULT '',
    price_updated_at        TIMESTAMPTZ,
    stock_updated_at        TIMESTAMPTZ,
    product_updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    data_quality_status     VARCHAR(32)  NOT NULL DEFAULT 'PARTIAL'
                            CHECK (data_quality_status IN (
                                'READY', 'PARTIAL', 'QUARANTINED', 'REJECTED'
                            )),
    price_freshness         VARCHAR(32)  NOT NULL DEFAULT 'UNVERIFIED',
    catalog_revision        BIGINT       NOT NULL DEFAULT 1,
    rebuilt_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_psp_merchant
    ON product_search_projection (merchant_id);

CREATE INDEX IF NOT EXISTS idx_psp_brand
    ON product_search_projection (brand_id)
    WHERE brand_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_psp_category
    ON product_search_projection (category_id)
    WHERE category_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_psp_price
    ON product_search_projection (current_price)
    WHERE current_price IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_psp_ready_active
    ON product_search_projection (data_quality_status, stock_status)
    WHERE data_quality_status IN ('READY', 'PARTIAL');

CREATE INDEX IF NOT EXISTS idx_psp_trgm_name
    ON product_search_projection
    USING gin (normalized_product_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_psp_trgm_search_doc
    ON product_search_projection
    USING gin (search_document gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_psp_fts
    ON product_search_projection
    USING gin (to_tsvector('simple', coalesce(search_document, '')));

-- ---------------------------------------------------------------------------
-- entity_search_index (dynamic fuzzy resolution catalog — no static maps)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS entity_search_index (
    id                  BIGSERIAL PRIMARY KEY,
    entity_type         VARCHAR(32)  NOT NULL
                        CHECK (entity_type IN (
                            'MERCHANT', 'FINANCIAL_INSTITUTION', 'BRAND',
                            'CATEGORY', 'PRODUCT', 'ATTRIBUTE', 'CITY'
                        )),
    entity_id           VARCHAR(128) NOT NULL,
    canonical_name      VARCHAR(512) NOT NULL,
    normalized_name     VARCHAR(512) NOT NULL,
    alias               VARCHAR(512) NOT NULL DEFAULT '',
    normalized_alias    VARCHAR(512) NOT NULL DEFAULT '',
    catalog_revision    BIGINT       NOT NULL DEFAULT 1,
    status              VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE', 'INACTIVE', 'QUARANTINED')),
    metadata            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    rebuilt_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (entity_type, entity_id, normalized_alias)
);

CREATE INDEX IF NOT EXISTS idx_esi_type_status
    ON entity_search_index (entity_type, status)
    WHERE status = 'ACTIVE';

CREATE INDEX IF NOT EXISTS idx_esi_norm_name_trgm
    ON entity_search_index
    USING gin (normalized_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_esi_norm_alias_trgm
    ON entity_search_index
    USING gin (normalized_alias gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_esi_canonical_exact
    ON entity_search_index (entity_type, normalized_name)
    WHERE status = 'ACTIVE';

-- ---------------------------------------------------------------------------
-- product_data_quality_projection (audit outcomes; does not delete products)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS product_data_quality_projection (
    product_id           BIGINT       PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    offer_id             BIGINT       REFERENCES product_offers(id) ON DELETE SET NULL,
    data_quality_status  VARCHAR(32)  NOT NULL
                         CHECK (data_quality_status IN (
                             'READY', 'PARTIAL', 'QUARANTINED', 'REJECTED'
                         )),
    score                NUMERIC(5,4) NOT NULL DEFAULT 0,
    chatbot_visible      BOOLEAN      NOT NULL DEFAULT FALSE,
    reasons              TEXT[]       NOT NULL DEFAULT '{}',
    empty_name           BOOLEAN      NOT NULL DEFAULT FALSE,
    missing_merchant     BOOLEAN      NOT NULL DEFAULT FALSE,
    missing_category     BOOLEAN      NOT NULL DEFAULT FALSE,
    missing_brand        BOOLEAN      NOT NULL DEFAULT FALSE,
    invalid_price        BOOLEAN      NOT NULL DEFAULT FALSE,
    invalid_currency     BOOLEAN      NOT NULL DEFAULT FALSE,
    invalid_url_format   BOOLEAN      NOT NULL DEFAULT FALSE,
    missing_primary_image BOOLEAN     NOT NULL DEFAULT FALSE,
    image_below_min_size BOOLEAN      NOT NULL DEFAULT FALSE,
    duplicate_external_id BOOLEAN     NOT NULL DEFAULT FALSE,
    duplicate_merchant_sku BOOLEAN    NOT NULL DEFAULT FALSE,
    invalid_gtin         BOOLEAN      NOT NULL DEFAULT FALSE,
    active_without_offer BOOLEAN      NOT NULL DEFAULT FALSE,
    in_stock_without_price BOOLEAN    NOT NULL DEFAULT FALSE,
    missing_price_updated_at BOOLEAN  NOT NULL DEFAULT FALSE,
    diagnostics          JSONB        NOT NULL DEFAULT '{}'::jsonb,
    audited_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pdq_status
    ON product_data_quality_projection (data_quality_status);

CREATE INDEX IF NOT EXISTS idx_pdq_visible
    ON product_data_quality_projection (chatbot_visible)
    WHERE chatbot_visible = TRUE;

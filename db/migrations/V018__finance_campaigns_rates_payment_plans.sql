-- V018: ADR-010 P3 — financial institutions, campaigns, rates, payment plans
-- Personalized credit approval remains CLOSED (ADR-009 Campaign Gate).
-- Missing rates must not be invented by application code.

-- ---------------------------------------------------------------------------
-- Institutions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS financial_institutions (
    id                  BIGSERIAL PRIMARY KEY,
    institution_code    VARCHAR(64)  NOT NULL UNIQUE,
    display_name        VARCHAR(256) NOT NULL,
    normalized_name     VARCHAR(256) NOT NULL,
    institution_type    VARCHAR(32)  NOT NULL DEFAULT 'BANK'
                        CHECK (institution_type IN (
                            'BANK', 'FINANCE_COMPANY', 'PARTICIPATION_BANK', 'OTHER'
                        )),
    status              VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE', 'INACTIVE')),
    metadata            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_financial_institutions_normalized
    ON financial_institutions (normalized_name)
    WHERE status = 'ACTIVE';

CREATE TABLE IF NOT EXISTS financial_institution_aliases (
    id               BIGSERIAL PRIMARY KEY,
    institution_id   BIGINT       NOT NULL REFERENCES financial_institutions(id) ON DELETE CASCADE,
    alias_text       VARCHAR(256) NOT NULL,
    normalized_alias VARCHAR(256) NOT NULL,
    locale           VARCHAR(16)  NOT NULL DEFAULT 'tr-TR',
    status           VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                     CHECK (status IN ('ACTIVE', 'INACTIVE')),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (normalized_alias, locale)
);

CREATE TABLE IF NOT EXISTS financial_institution_media (
    id               BIGSERIAL PRIMARY KEY,
    institution_id   BIGINT       NOT NULL REFERENCES financial_institutions(id) ON DELETE CASCADE,
    media_asset_id   BIGINT       NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    role             VARCHAR(32)  NOT NULL DEFAULT 'PRIMARY'
                     CHECK (role IN ('PRIMARY', 'LOGO', 'ICON')),
    is_primary       BOOLEAN      NOT NULL DEFAULT FALSE,
    valid_from       TIMESTAMPTZ,
    valid_until      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (institution_id, media_asset_id, role)
);

CREATE TABLE IF NOT EXISTS financial_products (
    id                   BIGSERIAL PRIMARY KEY,
    institution_id       BIGINT       NOT NULL REFERENCES financial_institutions(id) ON DELETE CASCADE,
    product_code         VARCHAR(64)  NOT NULL,
    display_name         VARCHAR(256) NOT NULL,
    product_type         VARCHAR(64)  NOT NULL DEFAULT 'INSTALLMENT'
                         CHECK (product_type IN (
                             'INSTALLMENT', 'CREDIT_CARD', 'CONSUMER_LOAN', 'OTHER'
                         )),
    status               VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                         CHECK (status IN ('ACTIVE', 'INACTIVE', 'DRAFT')),
    metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (institution_id, product_code)
);

CREATE TABLE IF NOT EXISTS merchant_financial_agreements (
    id                   BIGSERIAL PRIMARY KEY,
    merchant_id          BIGINT       NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    institution_id       BIGINT       NOT NULL REFERENCES financial_institutions(id) ON DELETE CASCADE,
    financial_product_id BIGINT       REFERENCES financial_products(id) ON DELETE SET NULL,
    status               VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                         CHECK (status IN ('ACTIVE', 'INACTIVE', 'EXPIRED', 'DRAFT')),
    valid_from           TIMESTAMPTZ,
    valid_until          TIMESTAMPTZ,
    source_reference     TEXT,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (merchant_id, institution_id, financial_product_id)
);

CREATE INDEX IF NOT EXISTS idx_merchant_financial_agreements_active
    ON merchant_financial_agreements (merchant_id, institution_id)
    WHERE status = 'ACTIVE';

-- ---------------------------------------------------------------------------
-- Campaigns
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS finance_campaigns (
    id                   BIGSERIAL PRIMARY KEY,
    institution_id       BIGINT       NOT NULL REFERENCES financial_institutions(id) ON DELETE RESTRICT,
    financial_product_id BIGINT       REFERENCES financial_products(id) ON DELETE SET NULL,
    campaign_code        VARCHAR(64)  NOT NULL UNIQUE,
    display_name         VARCHAR(256) NOT NULL,
    summary              TEXT,
    campaign_type        VARCHAR(64)  NOT NULL
                         CHECK (campaign_type IN (
                             'RATE_DISCOUNT', 'ZERO_RATE', 'DEFERRED_PAYMENT',
                             'INSTALLMENT', 'FEE_DISCOUNT', 'MERCHANT_SPECIAL',
                             'CATEGORY_SPECIAL', 'PRODUCT_SPECIAL'
                         )),
    status               VARCHAR(32)  NOT NULL DEFAULT 'DRAFT'
                         CHECK (status IN ('DRAFT', 'ACTIVE', 'INACTIVE', 'EXPIRED')),
    verification_status  VARCHAR(32)  NOT NULL DEFAULT 'UNVERIFIED'
                         CHECK (verification_status IN (
                             'UNVERIFIED', 'VERIFIED', 'REJECTED'
                         )),
    valid_from           TIMESTAMPTZ,
    valid_until          TIMESTAMPTZ,
    application_start_at TIMESTAMPTZ,
    application_end_at   TIMESTAMPTZ,
    minimum_purchase_amount NUMERIC(14,2),
    maximum_purchase_amount NUMERIC(14,2),
    membership_required  BOOLEAN      NOT NULL DEFAULT FALSE,
    source_reference     TEXT,
    metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_finance_campaigns_institution_status
    ON finance_campaigns (institution_id, status);

CREATE INDEX IF NOT EXISTS idx_finance_campaigns_valid_until
    ON finance_campaigns (valid_until)
    WHERE status = 'ACTIVE';

CREATE TABLE IF NOT EXISTS finance_campaign_versions (
    id               BIGSERIAL PRIMARY KEY,
    campaign_id      BIGINT       NOT NULL REFERENCES finance_campaigns(id) ON DELETE CASCADE,
    version_no       INTEGER      NOT NULL,
    snapshot         JSONB        NOT NULL,
    captured_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    source_reference TEXT,
    UNIQUE (campaign_id, version_no)
);

CREATE TABLE IF NOT EXISTS campaign_merchants (
    campaign_id      BIGINT NOT NULL REFERENCES finance_campaigns(id) ON DELETE CASCADE,
    merchant_id      BIGINT NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    PRIMARY KEY (campaign_id, merchant_id)
);

CREATE TABLE IF NOT EXISTS campaign_categories (
    campaign_id      BIGINT NOT NULL REFERENCES finance_campaigns(id) ON DELETE CASCADE,
    category_id      BIGINT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    PRIMARY KEY (campaign_id, category_id)
);

CREATE TABLE IF NOT EXISTS campaign_brands (
    campaign_id      BIGINT NOT NULL REFERENCES finance_campaigns(id) ON DELETE CASCADE,
    brand_id         BIGINT NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    PRIMARY KEY (campaign_id, brand_id)
);

CREATE TABLE IF NOT EXISTS campaign_products (
    campaign_id      BIGINT NOT NULL REFERENCES finance_campaigns(id) ON DELETE CASCADE,
    product_id       BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    PRIMARY KEY (campaign_id, product_id)
);

CREATE TABLE IF NOT EXISTS campaign_terms (
    id               BIGSERIAL PRIMARY KEY,
    campaign_id      BIGINT       NOT NULL REFERENCES finance_campaigns(id) ON DELETE CASCADE,
    term_months      INTEGER      NOT NULL CHECK (term_months > 0),
    included         BOOLEAN      NOT NULL DEFAULT TRUE,
    UNIQUE (campaign_id, term_months)
);

CREATE TABLE IF NOT EXISTS campaign_exclusions (
    id               BIGSERIAL PRIMARY KEY,
    campaign_id      BIGINT       NOT NULL REFERENCES finance_campaigns(id) ON DELETE CASCADE,
    exclusion_type   VARCHAR(64)  NOT NULL,
    exclusion_ref    VARCHAR(256) NOT NULL,
    detail           JSONB        NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS campaign_channels (
    id               BIGSERIAL PRIMARY KEY,
    campaign_id      BIGINT       NOT NULL REFERENCES finance_campaigns(id) ON DELETE CASCADE,
    channel_code     VARCHAR(64)  NOT NULL,
    UNIQUE (campaign_id, channel_code)
);

CREATE TABLE IF NOT EXISTS campaign_source_snapshots (
    id               BIGSERIAL PRIMARY KEY,
    campaign_id      BIGINT       NOT NULL REFERENCES finance_campaigns(id) ON DELETE CASCADE,
    content_hash     VARCHAR(128),
    payload          JSONB        NOT NULL,
    source_reference TEXT,
    captured_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Rate / fee snapshots
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS finance_rate_snapshots (
    id                   BIGSERIAL PRIMARY KEY,
    financial_product_id BIGINT       NOT NULL REFERENCES financial_products(id) ON DELETE CASCADE,
    campaign_id          BIGINT       REFERENCES finance_campaigns(id) ON DELETE SET NULL,
    merchant_id          BIGINT       REFERENCES merchants(id) ON DELETE SET NULL,
    category_id          BIGINT       REFERENCES categories(id) ON DELETE SET NULL,
    minimum_amount       NUMERIC(14,2),
    maximum_amount       NUMERIC(14,2),
    minimum_term         INTEGER,
    maximum_term         INTEGER,
    monthly_rate         NUMERIC(12,8),
    annual_cost_rate     NUMERIC(12,8),
    profit_rate          NUMERIC(12,8),
    rate_type            VARCHAR(32)  NOT NULL
                         CHECK (rate_type IN (
                             'INTEREST', 'PROFIT_RATE', 'FIXED_PAYMENT',
                             'ADVERTISED_PAYMENT', 'ZERO_RATE', 'UNKNOWN'
                         )),
    verification_status  VARCHAR(32)  NOT NULL DEFAULT 'UNVERIFIED'
                         CHECK (verification_status IN (
                             'UNVERIFIED', 'VERIFIED', 'REJECTED'
                         )),
    freshness_status     VARCHAR(32)  NOT NULL DEFAULT 'UNVERIFIED'
                         CHECK (freshness_status IN (
                             'FRESH', 'STALE', 'EXPIRED', 'UNVERIFIED', 'SOURCE_UNAVAILABLE'
                         )),
    valid_from           TIMESTAMPTZ,
    valid_until          TIMESTAMPTZ,
    captured_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    source_reference     TEXT,
    metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_finance_rate_snapshots_product_fresh
    ON finance_rate_snapshots (financial_product_id, freshness_status);

CREATE TABLE IF NOT EXISTS finance_rate_tiers (
    id               BIGSERIAL PRIMARY KEY,
    rate_snapshot_id BIGINT       NOT NULL REFERENCES finance_rate_snapshots(id) ON DELETE CASCADE,
    term_months      INTEGER      NOT NULL,
    monthly_rate     NUMERIC(12,8),
    fixed_payment    NUMERIC(14,2),
    UNIQUE (rate_snapshot_id, term_months)
);

CREATE TABLE IF NOT EXISTS finance_fee_snapshots (
    id               BIGSERIAL PRIMARY KEY,
    rate_snapshot_id BIGINT       REFERENCES finance_rate_snapshots(id) ON DELETE CASCADE,
    campaign_id      BIGINT       REFERENCES finance_campaigns(id) ON DELETE CASCADE,
    fee_type         VARCHAR(64)  NOT NULL,
    amount           NUMERIC(14,2),
    percent          NUMERIC(12,8),
    currency         VARCHAR(3)   NOT NULL DEFAULT 'TRY',
    captured_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    source_reference TEXT
);

-- ---------------------------------------------------------------------------
-- Payment plan persistence
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS payment_plan_calculations (
    id                   BIGSERIAL PRIMARY KEY,
    product_offer_id     BIGINT       REFERENCES product_offers(id) ON DELETE SET NULL,
    institution_id       BIGINT       REFERENCES financial_institutions(id) ON DELETE SET NULL,
    campaign_id          BIGINT       REFERENCES finance_campaigns(id) ON DELETE SET NULL,
    rate_snapshot_id     BIGINT       REFERENCES finance_rate_snapshots(id) ON DELETE SET NULL,
    plan_kind            VARCHAR(32)  NOT NULL
                         CHECK (plan_kind IN (
                             'CALCULATED_ESTIMATE', 'SOURCE_PROVIDED_OFFER'
                         )),
    purchase_price       NUMERIC(14,2) NOT NULL,
    down_payment         NUMERIC(14,2) NOT NULL DEFAULT 0,
    financed_amount      NUMERIC(14,2) NOT NULL,
    term_months          INTEGER      NOT NULL,
    monthly_rate         NUMERIC(12,8),
    fees_total           NUMERIC(14,2) NOT NULL DEFAULT 0,
    insurance_cost       NUMERIC(14,2) NOT NULL DEFAULT 0,
    monthly_payment      NUMERIC(14,2),
    total_repayment      NUMERIC(14,2),
    total_cost           NUMERIC(14,2),
    calculation_method   VARCHAR(64),
    display_label        VARCHAR(128) NOT NULL,
    status               VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                         CHECK (status IN ('ACTIVE', 'SUPERSEDED', 'INVALID')),
    calculated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_payment_plan_calculations_offer
    ON payment_plan_calculations (product_offer_id, term_months)
    WHERE status = 'ACTIVE';

CREATE TABLE IF NOT EXISTS payment_plan_installments (
    id                   BIGSERIAL PRIMARY KEY,
    calculation_id       BIGINT       NOT NULL REFERENCES payment_plan_calculations(id) ON DELETE CASCADE,
    installment_no       INTEGER      NOT NULL,
    payment_date         DATE,
    principal_amount     NUMERIC(14,2),
    finance_cost         NUMERIC(14,2),
    fee_amount           NUMERIC(14,2),
    total_installment    NUMERIC(14,2) NOT NULL,
    remaining_balance    NUMERIC(14,2),
    UNIQUE (calculation_id, installment_no)
);

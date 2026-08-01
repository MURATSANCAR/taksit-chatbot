-- V028: Recovery-P1 verification statuses + payment-plan idempotency
-- Staging-first; production apply only via separate deployment plan.

-- Campaign / rate verification statuses used by recovery manifests.
ALTER TABLE finance_campaigns
    DROP CONSTRAINT IF EXISTS finance_campaigns_verification_status_check;
ALTER TABLE finance_campaigns
    ADD CONSTRAINT finance_campaigns_verification_status_check
    CHECK (verification_status IN (
        'UNVERIFIED', 'SOURCE_PROVIDED', 'VERIFIED', 'CONFLICTED', 'EXPIRED', 'REJECTED'
    ));

ALTER TABLE finance_rate_snapshots
    DROP CONSTRAINT IF EXISTS finance_rate_snapshots_verification_status_check;
ALTER TABLE finance_rate_snapshots
    ADD CONSTRAINT finance_rate_snapshots_verification_status_check
    CHECK (verification_status IN (
        'UNVERIFIED', 'SOURCE_PROVIDED', 'VERIFIED', 'CONFLICTED', 'EXPIRED', 'REJECTED'
    ));

ALTER TABLE merchant_financial_agreements
    ADD COLUMN IF NOT EXISTS verification_status VARCHAR(32) NOT NULL DEFAULT 'UNVERIFIED';
ALTER TABLE merchant_financial_agreements
    DROP CONSTRAINT IF EXISTS merchant_financial_agreements_verification_status_check;
ALTER TABLE merchant_financial_agreements
    ADD CONSTRAINT merchant_financial_agreements_verification_status_check
    CHECK (verification_status IN (
        'UNVERIFIED', 'SOURCE_PROVIDED', 'VERIFIED', 'CONFLICTED', 'EXPIRED', 'REJECTED'
    ));

-- Idempotent payment plan identity (offer+institution+campaign+term+rate version).
ALTER TABLE payment_plan_calculations
    ADD COLUMN IF NOT EXISTS financial_product_id BIGINT
        REFERENCES financial_products(id) ON DELETE SET NULL;
ALTER TABLE payment_plan_calculations
    ADD COLUMN IF NOT EXISTS calculation_method_version VARCHAR(32)
        NOT NULL DEFAULT 'annuity_v1';
ALTER TABLE payment_plan_calculations
    ADD COLUMN IF NOT EXISTS verification_status VARCHAR(32)
        NOT NULL DEFAULT 'UNVERIFIED';
ALTER TABLE payment_plan_calculations
    ADD COLUMN IF NOT EXISTS valid_until TIMESTAMPTZ;
ALTER TABLE payment_plan_calculations
    ADD COLUMN IF NOT EXISTS first_payment_date DATE;

ALTER TABLE payment_plan_calculations
    DROP CONSTRAINT IF EXISTS payment_plan_calculations_verification_status_check;
ALTER TABLE payment_plan_calculations
    ADD CONSTRAINT payment_plan_calculations_verification_status_check
    CHECK (verification_status IN (
        'UNVERIFIED', 'SOURCE_PROVIDED', 'VERIFIED', 'CONFLICTED',
        'PAYMENT_PLAN_UNAVAILABLE', 'PAYMENT_PLAN_RECONCILIATION_FAILED', 'REJECTED'
    ));

-- Idempotency enforced in application layer (lookup-then-upsert).
CREATE INDEX IF NOT EXISTS idx_payment_plan_calc_idempotent_lookup
    ON payment_plan_calculations (
        product_offer_id,
        institution_id,
        term_months,
        calculation_method_version
    )
    WHERE status = 'ACTIVE';

-- Resolution audit (staging / recovery evidence; not user PII).
CREATE TABLE IF NOT EXISTS product_category_resolutions (
    id                   BIGSERIAL PRIMARY KEY,
    product_id           BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    source_category      TEXT,
    resolved_category_id BIGINT REFERENCES categories(id) ON DELETE SET NULL,
    resolution_method    VARCHAR(64) NOT NULL,
    confidence           VARCHAR(32) NOT NULL,
    evidence             TEXT,
    catalog_revision     TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_product_category_resolutions_product
    ON product_category_resolutions (product_id);

CREATE TABLE IF NOT EXISTS product_brand_resolutions (
    id                   BIGSERIAL PRIMARY KEY,
    product_id           BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    brand_id             BIGINT REFERENCES brands(id) ON DELETE SET NULL,
    source_method        VARCHAR(64) NOT NULL,
    confidence           VARCHAR(32) NOT NULL,
    evidence_span        TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_product_brand_resolutions_product
    ON product_brand_resolutions (product_id);

CREATE TABLE IF NOT EXISTS product_attribute_resolutions (
    id                       BIGSERIAL PRIMARY KEY,
    product_id               BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    attribute_key            VARCHAR(128) NOT NULL,
    attribute_definition_id  UUID,
    normalized_value         TEXT,
    unit                     VARCHAR(32),
    raw_value                TEXT,
    source                   VARCHAR(64) NOT NULL,
    confidence               VARCHAR(32) NOT NULL,
    evidence                 TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_product_attribute_resolutions_product
    ON product_attribute_resolutions (product_id);

-- Media HTTP validation quality flags (do not delete assets).
ALTER TABLE media_assets
    ADD COLUMN IF NOT EXISTS http_validation_status VARCHAR(32);
ALTER TABLE media_assets
    ADD COLUMN IF NOT EXISTS http_validation_detail JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE media_assets
    ADD COLUMN IF NOT EXISTS http_validated_at TIMESTAMPTZ;

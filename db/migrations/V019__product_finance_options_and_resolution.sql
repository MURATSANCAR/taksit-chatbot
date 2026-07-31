-- V019: ADR-010 P4 — product finance projection + entity resolution policy

CREATE TABLE IF NOT EXISTS entity_resolution_policies (
    id                  BIGSERIAL PRIMARY KEY,
    policy_code         VARCHAR(64)  NOT NULL UNIQUE,
    auto_select_min     NUMERIC(5,4) NOT NULL DEFAULT 0.92,
    clarify_min         NUMERIC(5,4) NOT NULL DEFAULT 0.78,
    max_candidates      INTEGER      NOT NULL DEFAULT 5,
    min_candidate_gap   NUMERIC(5,4) NOT NULL DEFAULT 0.05,
    status              VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE', 'INACTIVE', 'DRAFT')),
    metadata            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

INSERT INTO entity_resolution_policies (policy_code)
VALUES ('DEFAULT_ENTITY_RESOLUTION_V1')
ON CONFLICT (policy_code) DO NOTHING;

CREATE TABLE IF NOT EXISTS product_finance_options (
    id                   BIGSERIAL PRIMARY KEY,
    product_offer_id     BIGINT       NOT NULL REFERENCES product_offers(id) ON DELETE CASCADE,
    merchant_id          BIGINT       NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    institution_id       BIGINT       NOT NULL REFERENCES financial_institutions(id) ON DELETE CASCADE,
    financial_product_id BIGINT       REFERENCES financial_products(id) ON DELETE SET NULL,
    campaign_id          BIGINT       REFERENCES finance_campaigns(id) ON DELETE SET NULL,
    term_months          INTEGER      NOT NULL CHECK (term_months > 0),
    monthly_payment      NUMERIC(14,2),
    total_repayment      NUMERIC(14,2),
    fees_total           NUMERIC(14,2) NOT NULL DEFAULT 0,
    eligibility_status   VARCHAR(32)  NOT NULL DEFAULT 'ELIGIBLE'
                         CHECK (eligibility_status IN (
                             'ELIGIBLE', 'INELIGIBLE', 'UNKNOWN'
                         )),
    plan_kind            VARCHAR(32)
                         CHECK (plan_kind IS NULL OR plan_kind IN (
                             'CALCULATED_ESTIMATE', 'SOURCE_PROVIDED_OFFER'
                         )),
    price_snapshot_id    BIGINT,
    rate_snapshot_id     BIGINT       REFERENCES finance_rate_snapshots(id) ON DELETE SET NULL,
    payment_plan_id      BIGINT       REFERENCES payment_plan_calculations(id) ON DELETE SET NULL,
    freshness_status     VARCHAR(32)  NOT NULL DEFAULT 'UNVERIFIED'
                         CHECK (freshness_status IN (
                             'FRESH', 'STALE', 'EXPIRED', 'UNVERIFIED', 'SOURCE_UNAVAILABLE'
                         )),
    valid_until          TIMESTAMPTZ,
    calculated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (product_offer_id, institution_id, term_months, campaign_id)
);

CREATE INDEX IF NOT EXISTS idx_product_finance_options_offer
    ON product_finance_options (product_offer_id)
    WHERE eligibility_status = 'ELIGIBLE';

CREATE INDEX IF NOT EXISTS idx_product_finance_options_monthly
    ON product_finance_options (monthly_payment)
    WHERE eligibility_status = 'ELIGIBLE' AND monthly_payment IS NOT NULL;

CREATE TABLE IF NOT EXISTS ranking_mode_policies (
    id                  BIGSERIAL PRIMARY KEY,
    policy_code         VARCHAR(64)  NOT NULL UNIQUE,
    default_mode        VARCHAR(64)  NOT NULL DEFAULT 'BEST_OVERALL_VALUE'
                        CHECK (default_mode IN (
                            'CHEAPEST_PRODUCT_PRICE',
                            'LOWEST_MONTHLY_PAYMENT',
                            'LOWEST_TOTAL_REPAYMENT',
                            'LONGEST_TERM',
                            'BEST_ATTRIBUTE_MATCH',
                            'BEST_OVERALL_VALUE'
                        )),
    weights             JSONB        NOT NULL DEFAULT '{
                            "query_relevance": 0.25,
                            "attribute_coverage": 0.15,
                            "budget_compatibility": 0.15,
                            "stock": 0.10,
                            "price": 0.10,
                            "finance": 0.10,
                            "total_repayment": 0.10,
                            "freshness": 0.05
                        }'::jsonb,
    status              VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE', 'INACTIVE', 'DRAFT')),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

INSERT INTO ranking_mode_policies (policy_code)
VALUES ('DEFAULT_PRODUCT_RANKING_V1')
ON CONFLICT (policy_code) DO NOTHING;

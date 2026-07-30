-- V003: Kampanya kataloğu, uygunluk kuralları, ranking politikaları

CREATE TABLE IF NOT EXISTS merchants (
    id              BIGSERIAL PRIMARY KEY,
    merchant_code   VARCHAR(64)  NOT NULL UNIQUE,
    display_name    VARCHAR(256) NOT NULL,
    status          VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE', 'INACTIVE')),
    metadata        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS campaigns (
    id              BIGSERIAL PRIMARY KEY,
    campaign_code   VARCHAR(64)  NOT NULL UNIQUE,
    title           VARCHAR(256) NOT NULL,
    summary         TEXT         NOT NULL,
    category_id     BIGINT       NOT NULL REFERENCES categories(id),
    merchant_id     BIGINT       REFERENCES merchants(id),
    brand           VARCHAR(128),
    product_name    VARCHAR(256),
    list_price      NUMERIC(14,2),
    currency        VARCHAR(3)   NOT NULL DEFAULT 'TRY',
    installment_count INTEGER,
    monthly_payment NUMERIC(14,2),
    interest_rate   NUMERIC(8,4),
    cash_price      NUMERIC(14,2),
    min_budget      NUMERIC(14,2),
    max_budget      NUMERIC(14,2),
    membership_required BOOLEAN  NOT NULL DEFAULT TRUE,
    membership_cta_url  TEXT,
    membership_cta_label VARCHAR(128) DEFAULT 'Taksitlio''ya üye ol',
    starts_at       TIMESTAMPTZ,
    ends_at         TIMESTAMPTZ,
    status          VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE', 'INACTIVE', 'DRAFT', 'EXPIRED')),
    attributes      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    -- attributes örnek: camera_quality, weight_light, brand_tier
    search_text     TEXT         NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_campaigns_category_status
    ON campaigns (category_id, status);
CREATE INDEX IF NOT EXISTS idx_campaigns_budget
    ON campaigns (min_budget, max_budget)
    WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_campaigns_ends_at
    ON campaigns (ends_at)
    WHERE status = 'ACTIVE';

CREATE TABLE IF NOT EXISTS campaign_embeddings (
    id              BIGSERIAL PRIMARY KEY,
    campaign_id     BIGINT       NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    model_profile_id BIGINT      NOT NULL REFERENCES ai_model_profiles(id),
    embedding       DOUBLE PRECISION[] NOT NULL,
    embedding_dim   INTEGER      NOT NULL,
    source_text     TEXT         NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (campaign_id, model_profile_id)
);

CREATE TABLE IF NOT EXISTS eligibility_rule_sets (
    id              BIGSERIAL PRIMARY KEY,
    rule_set_code   VARCHAR(64)  NOT NULL UNIQUE,
    display_name    VARCHAR(128) NOT NULL,
    rules           JSONB        NOT NULL DEFAULT '[]'::jsonb,
    -- rules: [{ "type": "BUDGET_WITHIN", "params": {...} }, ...]
    status          VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE', 'INACTIVE', 'DRAFT')),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ranking_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_code     VARCHAR(64)  NOT NULL UNIQUE,
    display_name    VARCHAR(128) NOT NULL,
    weights         JSONB        NOT NULL DEFAULT '{
        "budget_fit": 0.35,
        "preference_fit": 0.25,
        "semantic_relevance": 0.20,
        "installment_fit": 0.15,
        "freshness": 0.05
    }'::jsonb,
    max_results     INTEGER      NOT NULL DEFAULT 5,
    status          VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE', 'INACTIVE', 'DRAFT')),
    configuration   JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

INSERT INTO eligibility_rule_sets (rule_set_code, display_name, rules) VALUES (
    'DEFAULT',
    'Varsayılan uygunluk kuralları',
    '[
        {"type": "STATUS_ACTIVE"},
        {"type": "WITHIN_DATE_WINDOW"},
        {"type": "BUDGET_COMPATIBLE"},
        {"type": "CATEGORY_MATCH"},
        {"type": "MONTHLY_PAYMENT_COMPATIBLE"}
    ]'::jsonb
) ON CONFLICT (rule_set_code) DO NOTHING;

INSERT INTO ranking_policies (policy_code, display_name) VALUES (
    'DEFAULT',
    'Varsayılan kampanya sıralama politikası'
) ON CONFLICT (policy_code) DO NOTHING;

INSERT INTO ai_model_profiles (
    profile_code, display_name, provider_type, endpoint_url, model_reference,
    task_type, context_limit, max_output_tokens, temperature, timeout_ms,
    parallel_slots, status, configuration
) VALUES (
    'RESPONSE_GENERATION',
    'Grounded Cevap Üretimi',
    'LLAMA_CPP',
    'http://127.0.0.1:8082/v1/chat/completions',
    'local-deep-understanding',
    'RESPONSE',
    4096,
    512,
    0.200,
    5000,
    2,
    'ACTIVE',
    '{"thinking_enabled":false,"streaming_enabled":false,"json_schema_required":false,"grounded":true}'::jsonb
)
ON CONFLICT (profile_code) DO NOTHING;

INSERT INTO ai_task_routes (
    task_code,
    primary_model_profile_id,
    fallback_model_profile_id,
    confidence_policy_id,
    timeout_policy_id,
    status
)
SELECT
    'RESPONSE_GENERATION',
    resp.id,
    NULL,
    NULL,
    NULL,
    'ACTIVE'
FROM ai_model_profiles resp
WHERE resp.profile_code = 'RESPONSE_GENERATION'
ON CONFLICT (task_code) DO NOTHING;

-- Demo merchant + kampanyalar
INSERT INTO merchants (merchant_code, display_name) VALUES
    ('DEMO_TECH', 'Demo Teknoloji Mağazası'),
    ('DEMO_HOME', 'Demo Ev Mağazası')
ON CONFLICT (merchant_code) DO NOTHING;

INSERT INTO campaigns (
    campaign_code, title, summary, category_id, merchant_id, brand, product_name,
    list_price, installment_count, monthly_payment, cash_price,
    min_budget, max_budget, attributes, search_text, starts_at, ends_at, status
)
SELECT
    'PHONE_CAM_40K',
    'Kamera odaklı telefon — 12 taksit',
    'Yüksek kamera kaliteli akıllı telefon, 12 aya varan taksit fırsatı',
    c.id,
    m.id,
    'DemoBrand',
    'CamPhone X',
    39999.00,
    12,
    3333.00,
    37999.00,
    30000.00,
    45000.00,
    '{"camera_quality":0.95,"installment":0.9,"weight_light":0.4}'::jsonb,
    'kamera kaliteli akıllı telefon taksit cep telefonu',
    NOW() - INTERVAL '7 days',
    NOW() + INTERVAL '90 days',
    'ACTIVE'
FROM categories c
JOIN merchants m ON m.merchant_code = 'DEMO_TECH'
WHERE c.category_code = 'MOBILE_PHONE'
ON CONFLICT (campaign_code) DO NOTHING;

INSERT INTO campaigns (
    campaign_code, title, summary, category_id, merchant_id, brand, product_name,
    list_price, installment_count, monthly_payment, cash_price,
    min_budget, max_budget, attributes, search_text, starts_at, ends_at, status
)
SELECT
    'PHONE_BUDGET_35K',
    'Uygun fiyatlı telefon — düşük aylık ödeme',
    'Bütçe dostu telefon, düşük aylık taksit',
    c.id,
    m.id,
    'DemoBrand',
    'ValuePhone 12',
    32999.00,
    12,
    2749.00,
    30999.00,
    20000.00,
    36000.00,
    '{"camera_quality":0.6,"installment":0.95,"weight_light":0.7}'::jsonb,
    'uygun fiyatlı telefon düşük taksit aylık ödeme',
    NOW() - INTERVAL '7 days',
    NOW() + INTERVAL '90 days',
    'ACTIVE'
FROM categories c
JOIN merchants m ON m.merchant_code = 'DEMO_TECH'
WHERE c.category_code = 'MOBILE_PHONE'
ON CONFLICT (campaign_code) DO NOTHING;

INSERT INTO campaigns (
    campaign_code, title, summary, category_id, merchant_id, brand, product_name,
    list_price, installment_count, monthly_payment, cash_price,
    min_budget, max_budget, attributes, search_text, starts_at, ends_at, status
)
SELECT
    'LAPTOP_SCHOOL_25K',
    'Okul için hafif laptop',
    'Üniversite ve okul kullanımı için hafif dizüstü bilgisayar',
    c.id,
    m.id,
    'DemoBrand',
    'LiteBook S',
    24999.00,
    9,
    2777.00,
    23999.00,
    15000.00,
    30000.00,
    '{"weight_light":0.95,"installment":0.8,"camera_quality":0.3}'::jsonb,
    'okul üniversite hafif laptop dizüstü bilgisayar',
    NOW() - INTERVAL '7 days',
    NOW() + INTERVAL '90 days',
    'ACTIVE'
FROM categories c
JOIN merchants m ON m.merchant_code = 'DEMO_TECH'
WHERE c.category_code = 'LAPTOP'
ON CONFLICT (campaign_code) DO NOTHING;

INSERT INTO campaigns (
    campaign_code, title, summary, category_id, merchant_id, brand, product_name,
    list_price, installment_count, monthly_payment, cash_price,
    min_budget, max_budget, attributes, search_text, starts_at, ends_at, status
)
SELECT
    'TABLET_SCHOOL_18K',
    'Okul için tablet',
    'Hafif tablet, ders ve not alma için uygun',
    c.id,
    m.id,
    'DemoBrand',
    'StudyPad',
    17999.00,
    6,
    2999.00,
    16999.00,
    10000.00,
    22000.00,
    '{"weight_light":0.9,"installment":0.7}'::jsonb,
    'okul tablet hafif ders',
    NOW() - INTERVAL '7 days',
    NOW() + INTERVAL '90 days',
    'ACTIVE'
FROM categories c
JOIN merchants m ON m.merchant_code = 'DEMO_TECH'
WHERE c.category_code = 'TABLET'
ON CONFLICT (campaign_code) DO NOTHING;

-- V006: Conversation state policies (technical defaults only).
-- Note: V003–V005 already used by category/campaign/prompt migrations.
-- No category, brand, bank, or campaign seeds.

CREATE TABLE IF NOT EXISTS conversation_state_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_code     VARCHAR(64)  NOT NULL UNIQUE,
    display_name    VARCHAR(128) NOT NULL,
    anonymous_idle_ttl_seconds INTEGER NOT NULL DEFAULT 1800
                    CHECK (anonymous_idle_ttl_seconds > 0),
    authenticated_idle_ttl_seconds INTEGER NOT NULL DEFAULT 86400
                    CHECK (authenticated_idle_ttl_seconds > 0),
    absolute_lifetime_seconds INTEGER NOT NULL DEFAULT 604800
                    CHECK (absolute_lifetime_seconds > 0),
    idempotency_ttl_seconds INTEGER NOT NULL DEFAULT 604800
                    CHECK (idempotency_ttl_seconds > 0),
    max_state_size_bytes INTEGER NOT NULL DEFAULT 65536
                    CHECK (max_state_size_bytes > 0),
    max_preferences INTEGER NOT NULL DEFAULT 32
                    CHECK (max_preferences > 0),
    max_entities INTEGER NOT NULL DEFAULT 32
                    CHECK (max_entities > 0),
    max_ambiguities INTEGER NOT NULL DEFAULT 16
                    CHECK (max_ambiguities > 0),
    max_category_candidates INTEGER NOT NULL DEFAULT 8
                    CHECK (max_category_candidates > 0),
    max_metadata_bytes INTEGER NOT NULL DEFAULT 4096
                    CHECK (max_metadata_bytes > 0),
    max_string_length INTEGER NOT NULL DEFAULT 500
                    CHECK (max_string_length > 0),
    status          VARCHAR(32) NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE', 'INACTIVE', 'DRAFT')),
    configuration   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO conversation_state_policies (
    policy_code,
    display_name,
    anonymous_idle_ttl_seconds,
    authenticated_idle_ttl_seconds,
    absolute_lifetime_seconds,
    idempotency_ttl_seconds,
    max_state_size_bytes,
    max_preferences,
    max_entities,
    max_ambiguities,
    max_category_candidates,
    max_metadata_bytes,
    max_string_length,
    status
) VALUES (
    'CONVERSATION_DEFAULT',
    'Varsayılan conversation state politikası',
    1800,
    86400,
    604800,
    604800,
    65536,
    32,
    32,
    16,
    8,
    4096,
    500,
    'ACTIVE'
) ON CONFLICT (policy_code) DO NOTHING;

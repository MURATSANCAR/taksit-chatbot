-- V008: Semantic match policies for dynamic category catalog.
-- NEW table (distinct from V003 category_match_policies).
-- Seed contains the DEFAULT policy only. No category seeds are added here.

CREATE TABLE IF NOT EXISTS semantic_match_policies (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_code                 VARCHAR(64)  NOT NULL UNIQUE,
    display_name                VARCHAR(128) NOT NULL,
    minimum_score               NUMERIC(4,3) NOT NULL DEFAULT 0.550
                                CHECK (minimum_score >= 0.0 AND minimum_score <= 1.0),
    clarify_score_gap           NUMERIC(4,3) NOT NULL DEFAULT 0.080
                                CHECK (clarify_score_gap >= 0.0 AND clarify_score_gap <= 1.0),
    maximum_candidates          INTEGER      NOT NULL DEFAULT 3
                                CHECK (maximum_candidates >= 1),
    alias_weight                NUMERIC(4,3) NOT NULL DEFAULT 0.350
                                CHECK (alias_weight >= 0.0 AND alias_weight <= 1.0),
    lexical_weight              NUMERIC(4,3) NOT NULL DEFAULT 0.150
                                CHECK (lexical_weight >= 0.0 AND lexical_weight <= 1.0),
    vector_weight               NUMERIC(4,3) NOT NULL DEFAULT 0.350
                                CHECK (vector_weight >= 0.0 AND vector_weight <= 1.0),
    use_case_weight             NUMERIC(4,3) NOT NULL DEFAULT 0.100
                                CHECK (use_case_weight >= 0.0 AND use_case_weight <= 1.0),
    hierarchy_weight            NUMERIC(4,3) NOT NULL DEFAULT 0.050
                                CHECK (hierarchy_weight >= 0.0 AND hierarchy_weight <= 1.0),
    allow_lexical_degraded_mode BOOLEAN      NOT NULL DEFAULT TRUE,
    cache_ttl_seconds           INTEGER      NOT NULL DEFAULT 300
                                CHECK (cache_ttl_seconds >= 0),
    require_semantic_description BOOLEAN     NOT NULL DEFAULT TRUE,
    max_depth                   INTEGER      NOT NULL DEFAULT 4
                                CHECK (max_depth >= 1),
    fuzzy_min_similarity        NUMERIC(4,3) NOT NULL DEFAULT 0.780
                                CHECK (fuzzy_min_similarity >= 0.0 AND fuzzy_min_similarity <= 1.0),
    policy_version              INTEGER      NOT NULL DEFAULT 1
                                CHECK (policy_version >= 1),
    status                      VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                                CHECK (status IN ('ACTIVE', 'INACTIVE', 'DRAFT')),
    configuration               JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

INSERT INTO semantic_match_policies (
    policy_code,
    display_name
) VALUES (
    'CATEGORY_MATCH_DEFAULT',
    'Varsayılan semantic category matcher politikası'
) ON CONFLICT (policy_code) DO NOTHING;

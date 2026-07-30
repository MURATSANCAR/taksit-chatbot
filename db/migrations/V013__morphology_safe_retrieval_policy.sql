-- V013: Morphology-safe retrieval policy (ADR-008 P0).
-- Adds per-channel alias weight columns + token-set thresholds to
-- semantic_match_policies so the ADR-008 signals (surface / normalized /
-- token-set / prefix-safe / character n-gram / morphological variant)
-- are tunable per deployment without redeploying the matcher.
--
-- Follows V011 additive-columns pattern. NO category / alias / catalog
-- seeds. Existing rows pick up the SemanticMatchPolicy defaults.

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS surface_exact_alias_weight NUMERIC(4,3) NOT NULL DEFAULT 1.000
        CHECK (surface_exact_alias_weight >= 0.0 AND surface_exact_alias_weight <= 1.0);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS normalized_exact_alias_weight NUMERIC(4,3) NOT NULL DEFAULT 0.950
        CHECK (normalized_exact_alias_weight >= 0.0 AND normalized_exact_alias_weight <= 1.0);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS token_set_alias_weight NUMERIC(4,3) NOT NULL DEFAULT 0.850
        CHECK (token_set_alias_weight >= 0.0 AND token_set_alias_weight <= 1.0);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS prefix_safe_alias_weight NUMERIC(4,3) NOT NULL DEFAULT 0.750
        CHECK (prefix_safe_alias_weight >= 0.0 AND prefix_safe_alias_weight <= 1.0);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS character_ngram_weight NUMERIC(4,3) NOT NULL DEFAULT 0.450
        CHECK (character_ngram_weight >= 0.0 AND character_ngram_weight <= 1.0);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS morphological_variant_weight NUMERIC(4,3) NOT NULL DEFAULT 0.550
        CHECK (morphological_variant_weight >= 0.0 AND morphological_variant_weight <= 1.0);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS character_ngram_min_similarity NUMERIC(4,3) NOT NULL DEFAULT 0.780
        CHECK (character_ngram_min_similarity >= 0.0 AND character_ngram_min_similarity <= 1.0);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS character_ngram_min_token_length INTEGER NOT NULL DEFAULT 4
        CHECK (character_ngram_min_token_length >= 1);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS morphological_variant_min_length INTEGER NOT NULL DEFAULT 4
        CHECK (morphological_variant_min_length >= 1);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS token_set_min_overlap NUMERIC(4,3) NOT NULL DEFAULT 1.000
        CHECK (token_set_min_overlap >= 0.0 AND token_set_min_overlap <= 1.0);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS surface_exact_can_auto_select BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS token_set_can_auto_select BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS morphological_variant_can_auto_select BOOLEAN NOT NULL DEFAULT FALSE;

-- Seed the DEFAULT policy's ``configuration`` blob with the ADR-008
-- weights so any code path that reads configuration overrides (V008
-- style) still sees the correct values. No category / alias data is
-- introduced by this migration.
UPDATE semantic_match_policies
SET configuration = COALESCE(configuration, '{}'::jsonb) || jsonb_build_object(
        'surface_exact_alias_weight', 1.0,
        'normalized_exact_alias_weight', 0.95,
        'token_set_alias_weight', 0.85,
        'prefix_safe_alias_weight', 0.75,
        'character_ngram_weight', 0.45,
        'morphological_variant_weight', 0.55,
        'character_ngram_min_similarity', 0.78,
        'character_ngram_min_token_length', 4,
        'morphological_variant_min_length', 4,
        'token_set_min_overlap', 1.0,
        'surface_exact_can_auto_select', TRUE,
        'token_set_can_auto_select', FALSE,
        'morphological_variant_can_auto_select', FALSE
    )
WHERE policy_code = 'CATEGORY_MATCH_DEFAULT';

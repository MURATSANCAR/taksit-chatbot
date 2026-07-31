-- V014: Top-K diversification + soft sibling exclusion + concept coverage
-- (ADR-008 P0.1). Adds the ranking-only channels required to close out
-- the residual E2E gap without lowering quality thresholds. All fields
-- are ranking-side; auto-select gating (V013) is unchanged.
--
-- Follows V011/V013 additive-columns pattern. NO category / alias /
-- catalog seeds. Existing rows pick up SemanticMatchPolicy defaults.

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS sibling_soft_exclusion_factor NUMERIC(4,3) NOT NULL DEFAULT 0.200
        CHECK (sibling_soft_exclusion_factor >= 0.0 AND sibling_soft_exclusion_factor <= 1.0);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS concept_coverage_weight NUMERIC(4,3) NOT NULL DEFAULT 0.100
        CHECK (concept_coverage_weight >= 0.0 AND concept_coverage_weight <= 1.0);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS diversification_enabled BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS same_parent_penalty NUMERIC(4,3) NOT NULL DEFAULT 0.060
        CHECK (same_parent_penalty >= 0.0 AND same_parent_penalty <= 1.0);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS prefer_positive_channel_in_topk BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS sibling_diversity_enabled BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS force_parent_child_collapse_on_direct_alias BOOLEAN NOT NULL DEFAULT TRUE;

-- character_ngram_weight was seeded at 0.45 by V013. ADR-008 P0.1 keeps
-- it purely ranking-side (auto-select still gated) and bumps the
-- default to 0.50 to help character-neighbour recall for correct
-- morphological variants. We only rewrite rows that still carry the
-- V013 seed value; operator-set values are preserved.
UPDATE semantic_match_policies
SET character_ngram_weight = 0.500
WHERE character_ngram_weight = 0.450
  AND policy_code = 'CATEGORY_MATCH_DEFAULT';

-- Seed the DEFAULT policy's ``configuration`` blob so any code path
-- that reads configuration overrides (V008 style) sees the ADR-008 P0.1
-- ranking weights.  Safety-critical gating (auto_select_*) is untouched.
UPDATE semantic_match_policies
SET configuration = COALESCE(configuration, '{}'::jsonb) || jsonb_build_object(
        'sibling_soft_exclusion_factor', 0.20,
        'concept_coverage_weight', 0.10,
        'diversification_enabled', TRUE,
        'same_parent_penalty', 0.06,
        'prefer_positive_channel_in_topk', TRUE,
        'sibling_diversity_enabled', TRUE,
        'force_parent_child_collapse_on_direct_alias', TRUE,
        'character_ngram_weight', 0.50
    )
WHERE policy_code = 'CATEGORY_MATCH_DEFAULT';

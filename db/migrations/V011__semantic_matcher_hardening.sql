-- V011: Semantic matcher hardening (ADR-006).
-- Additive columns for the new SemanticMatchPolicy hardening fields
-- plus conversation state policy limits for semantic_constraints.
-- No category / brand / bank seeds — only additive DDL.

-- Semantic match policy hardening fields.
ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS candidate_pool_size INTEGER NOT NULL DEFAULT 25
        CHECK (candidate_pool_size >= 1);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS direct_alias_boost NUMERIC(4,3) NOT NULL DEFAULT 0.150
        CHECK (direct_alias_boost >= 0.0 AND direct_alias_boost <= 1.0);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS exact_alias_boost NUMERIC(4,3) NOT NULL DEFAULT 0.200
        CHECK (exact_alias_boost >= 0.0 AND exact_alias_boost <= 1.0);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS negative_semantic_weight NUMERIC(4,3) NOT NULL DEFAULT 0.350
        CHECK (negative_semantic_weight >= 0.0 AND negative_semantic_weight <= 1.0);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS explicit_negative_penalty NUMERIC(4,3) NOT NULL DEFAULT 0.900
        CHECK (explicit_negative_penalty >= 0.0 AND explicit_negative_penalty <= 1.0);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS correction_penalty NUMERIC(4,3) NOT NULL DEFAULT 0.950
        CHECK (correction_penalty >= 0.0 AND correction_penalty <= 1.0);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS hard_exclude_exact_negative_alias BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS hard_exclude_user_correction BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS negative_match_threshold NUMERIC(4,3) NOT NULL DEFAULT 0.750
        CHECK (negative_match_threshold >= 0.0 AND negative_match_threshold <= 1.0);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS parent_child_collapse_enabled BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS parent_child_collapse_gap NUMERIC(4,3) NOT NULL DEFAULT 0.120
        CHECK (parent_child_collapse_gap >= 0.0 AND parent_child_collapse_gap <= 1.0);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS direct_alias_can_reduce_ambiguity BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS direct_alias_minimum_weight NUMERIC(4,3) NOT NULL DEFAULT 0.850
        CHECK (direct_alias_minimum_weight >= 0.0 AND direct_alias_minimum_weight <= 1.0);

ALTER TABLE semantic_match_policies
    ADD COLUMN IF NOT EXISTS direct_alias_conflict_requires_clarification BOOLEAN NOT NULL DEFAULT TRUE;

-- Conversation state policy: semantic_constraints size caps.
ALTER TABLE conversation_state_policies
    ADD COLUMN IF NOT EXISTS max_positive_constraints INTEGER NOT NULL DEFAULT 16
        CHECK (max_positive_constraints >= 0);

ALTER TABLE conversation_state_policies
    ADD COLUMN IF NOT EXISTS max_negative_constraints INTEGER NOT NULL DEFAULT 16
        CHECK (max_negative_constraints >= 0);

ALTER TABLE conversation_state_policies
    ADD COLUMN IF NOT EXISTS max_corrections INTEGER NOT NULL DEFAULT 8
        CHECK (max_corrections >= 0);

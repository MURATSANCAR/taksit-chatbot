-- V024: ADR-012 — sponsored placements (commercial ranking isolation).
-- Sponsored weight cannot steal organic "en uygun" (enforced in ranking code).

CREATE TABLE IF NOT EXISTS sponsored_placements (
    id              BIGSERIAL PRIMARY KEY,
    product_id      VARCHAR(128) NOT NULL UNIQUE,
    weight          NUMERIC(12,4) NOT NULL DEFAULT 0,
    merchant_id     VARCHAR(128),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    label           VARCHAR(64)  NOT NULL DEFAULT 'sponsored',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sponsored_placements_active
    ON sponsored_placements (is_active)
    WHERE is_active = TRUE;

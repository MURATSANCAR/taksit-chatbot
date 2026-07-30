-- V007: Dynamic category catalog schema.
-- No category seeds. Legacy V003 tables (categories/category_embeddings/
-- category_match_policies) intentionally left in place — new names below.
-- All identifiers are UUID (stored as TEXT for portability across CI without
-- uuid-ossp).

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS category_catalogs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    catalog_code        VARCHAR(64)  NOT NULL UNIQUE,
    display_name        VARCHAR(128) NOT NULL,
    primary_locale      VARCHAR(16)  NOT NULL DEFAULT 'tr-TR',
    alternate_locales   TEXT[]       NOT NULL DEFAULT '{}',
    match_policy_code   VARCHAR(64)  NOT NULL,
    status              VARCHAR(32)  NOT NULL DEFAULT 'DRAFT'
                        CHECK (status IN ('DRAFT', 'ACTIVE', 'INACTIVE', 'ARCHIVED')),
    published_revision  INTEGER      NOT NULL DEFAULT 0
                        CHECK (published_revision >= 0),
    draft_revision      INTEGER      NOT NULL DEFAULT 0
                        CHECK (draft_revision >= 0),
    metadata            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_category_catalogs_status
    ON category_catalogs (status);

CREATE TABLE IF NOT EXISTS catalog_categories (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    catalog_id            UUID NOT NULL REFERENCES category_catalogs(id) ON DELETE CASCADE,
    parent_id             UUID REFERENCES catalog_categories(id) ON DELETE RESTRICT,
    external_code         VARCHAR(128),
    slug                  VARCHAR(128) NOT NULL,
    depth                 INTEGER      NOT NULL DEFAULT 0
                          CHECK (depth >= 0),
    ordering              INTEGER      NOT NULL DEFAULT 0,
    status                VARCHAR(32)  NOT NULL DEFAULT 'DRAFT'
                          CHECK (status IN ('DRAFT', 'ACTIVE', 'INACTIVE', 'ARCHIVED')),
    semantic_description  TEXT         NOT NULL DEFAULT '',
    introduced_revision   INTEGER      NOT NULL DEFAULT 0,
    retired_revision      INTEGER,
    metadata              JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (catalog_id, slug)
);

CREATE INDEX IF NOT EXISTS idx_catalog_categories_catalog
    ON catalog_categories (catalog_id);
CREATE INDEX IF NOT EXISTS idx_catalog_categories_parent
    ON catalog_categories (parent_id);
CREATE INDEX IF NOT EXISTS idx_catalog_categories_status
    ON catalog_categories (status);

CREATE TABLE IF NOT EXISTS category_localizations (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id    UUID NOT NULL REFERENCES catalog_categories(id) ON DELETE CASCADE,
    locale         VARCHAR(16)  NOT NULL,
    display_name   VARCHAR(256) NOT NULL,
    description    TEXT         NOT NULL DEFAULT '',
    synonyms       TEXT[]       NOT NULL DEFAULT '{}',
    status         VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                   CHECK (status IN ('DRAFT', 'ACTIVE', 'INACTIVE')),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (category_id, locale)
);

CREATE TABLE IF NOT EXISTS category_aliases (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id    UUID NOT NULL REFERENCES catalog_categories(id) ON DELETE CASCADE,
    locale         VARCHAR(16)  NOT NULL,
    alias_text     VARCHAR(256) NOT NULL,
    alias_type     VARCHAR(32)  NOT NULL DEFAULT 'EXACT'
                   CHECK (alias_type IN ('EXACT', 'PREFIX', 'FUZZY', 'SEMANTIC_HINT')),
    weight         NUMERIC(4,3) NOT NULL DEFAULT 1.000
                   CHECK (weight >= 0.0 AND weight <= 1.0),
    status         VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                   CHECK (status IN ('ACTIVE', 'INACTIVE')),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_category_aliases_active
    ON category_aliases (category_id, locale, lower(alias_text))
    WHERE status = 'ACTIVE';

CREATE INDEX IF NOT EXISTS idx_category_aliases_lookup
    ON category_aliases (locale, alias_type)
    WHERE status = 'ACTIVE';

CREATE TABLE IF NOT EXISTS category_use_cases (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id    UUID NOT NULL REFERENCES catalog_categories(id) ON DELETE CASCADE,
    locale         VARCHAR(16)  NOT NULL,
    use_case_text  TEXT         NOT NULL,
    status         VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                   CHECK (status IN ('ACTIVE', 'INACTIVE')),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_category_use_cases_lookup
    ON category_use_cases (category_id, locale)
    WHERE status = 'ACTIVE';

-- attribute_definition_id is a UUID reference. Attribute catalog itself is
-- managed by the customer schema outside V007 scope; a link table is enough.
CREATE TABLE IF NOT EXISTS category_attribute_links (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id               UUID NOT NULL REFERENCES catalog_categories(id) ON DELETE CASCADE,
    attribute_definition_id   UUID NOT NULL,
    importance                NUMERIC(4,3) NOT NULL DEFAULT 0.500
                              CHECK (importance >= 0.0 AND importance <= 1.0),
    status                    VARCHAR(32) NOT NULL DEFAULT 'ACTIVE'
                              CHECK (status IN ('ACTIVE', 'INACTIVE')),
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (category_id, attribute_definition_id)
);

CREATE TABLE IF NOT EXISTS catalog_revisions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    catalog_id     UUID NOT NULL REFERENCES category_catalogs(id) ON DELETE CASCADE,
    revision       INTEGER NOT NULL,
    status         VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
                   CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
    published_at   TIMESTAMPTZ,
    notes          TEXT,
    validation_report JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (catalog_id, revision)
);

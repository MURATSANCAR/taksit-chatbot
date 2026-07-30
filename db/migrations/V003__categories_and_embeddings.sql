-- V002: Dinamik kategori kataloğu + embedding profili
-- Kategori kodları ve eşikler kodda sabitlenmez.

CREATE TABLE IF NOT EXISTS categories (
    id              BIGSERIAL PRIMARY KEY,
    category_code   VARCHAR(64)  NOT NULL UNIQUE,
    display_name    VARCHAR(128) NOT NULL,
    description     TEXT         NOT NULL,
    parent_id       BIGINT       REFERENCES categories(id),
    synonyms        TEXT[]       NOT NULL DEFAULT '{}',
    status          VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE', 'INACTIVE', 'DRAFT')),
    metadata        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_categories_status ON categories (status);
CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories (parent_id);

CREATE TABLE IF NOT EXISTS category_embeddings (
    id              BIGSERIAL PRIMARY KEY,
    category_id     BIGINT       NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    model_profile_id BIGINT      NOT NULL REFERENCES ai_model_profiles(id),
    embedding       DOUBLE PRECISION[] NOT NULL,
    embedding_dim   INTEGER      NOT NULL,
    source_text     TEXT         NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (category_id, model_profile_id)
);

CREATE INDEX IF NOT EXISTS idx_category_embeddings_profile
    ON category_embeddings (model_profile_id);

CREATE TABLE IF NOT EXISTS category_match_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_code     VARCHAR(64)  NOT NULL UNIQUE,
    display_name    VARCHAR(128) NOT NULL,
    minimum_score   NUMERIC(4,3) NOT NULL DEFAULT 0.55,
    maximum_candidates INTEGER   NOT NULL DEFAULT 3,
    clarify_score_gap NUMERIC(4,3) NOT NULL DEFAULT 0.08,
    status          VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE', 'INACTIVE', 'DRAFT')),
    configuration   JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Embedding model + deployment bootstrap: db/bootstrap/*.sql (not in schema migrations).

INSERT INTO category_match_policies (policy_code, display_name)
VALUES ('DEFAULT', 'Varsayılan kategori eşleştirme politikası')
ON CONFLICT (policy_code) DO NOTHING;

-- MVP seed kategorileri (katalog DB'den yönetilir)
INSERT INTO categories (category_code, display_name, description, synonyms) VALUES
(
    'MOBILE_PHONE',
    'Cep Telefonu',
    'Akıllı telefon, mobil cihaz, cep telefonu ürünleri',
    ARRAY['telefon', 'cep telefonu', 'akıllı telefon', 'mobil', 'iphone', 'samsung']
),
(
    'LAPTOP',
    'Dizüstü Bilgisayar',
    'Laptop, notebook, dizüstü bilgisayar ürünleri',
    ARRAY['laptop', 'notebook', 'dizüstü', 'bilgisayar', 'pc']
),
(
    'TABLET',
    'Tablet',
    'Tablet bilgisayar ve benzeri taşınabilir ekranlı cihazlar',
    ARRAY['tablet', 'ipad']
),
(
    'HOME_APPLIANCE',
    'Beyaz Eşya',
    'Buzdolabı, çamaşır makinesi ve diğer ev aletleri',
    ARRAY['beyaz eşya', 'buzdolabı', 'çamaşır makinesi', 'ev aleti']
),
(
    'FURNITURE',
    'Mobilya',
    'Ev ve ofis mobilyası ürünleri',
    ARRAY['mobilya', 'koltuk', 'yatak', 'masa']
)
ON CONFLICT (category_code) DO NOTHING;

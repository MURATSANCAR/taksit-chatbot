-- Link merchant products to dynamic categories learned from feeds (ADR-010).
ALTER TABLE products
    ADD COLUMN IF NOT EXISTS category_id BIGINT REFERENCES categories(id);

CREATE INDEX IF NOT EXISTS idx_products_category_id
    ON products (category_id)
    WHERE category_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_products_brand_id
    ON products (brand_id)
    WHERE brand_id IS NOT NULL;

-- Footwear category for merchant catalogs (e.g. FLO) that sell ayakkabı.
INSERT INTO categories (category_code, display_name, description, synonyms) VALUES
(
    'FOOTWEAR',
    'Ayakkabı',
    'Spor, klasik, bot, sandalette ayakkabı ve benzeri ürünler',
    ARRAY[
        'ayakkabı',
        'ayakkabi',
        'spor ayakkabı',
        'spor ayakkabi',
        'sneaker',
        'bot',
        'terlik',
        'sandalet',
        'footwear'
    ]
)
ON CONFLICT (category_code) DO NOTHING;

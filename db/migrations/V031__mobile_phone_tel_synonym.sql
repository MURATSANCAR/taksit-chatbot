-- Colloquial TR chat abbreviation "tel" → MOBILE_PHONE via catalog synonyms
-- (ADR-010 §32: no static query→entity maps in application code).
UPDATE categories
SET synonyms = (
        SELECT array_agg(DISTINCT s)
        FROM unnest(
            COALESCE(synonyms, '{}'::text[]) || ARRAY['tel']
        ) AS s
    ),
    updated_at = NOW()
WHERE category_code = 'MOBILE_PHONE'
  AND NOT ('tel' = ANY (COALESCE(synonyms, '{}'::text[])));

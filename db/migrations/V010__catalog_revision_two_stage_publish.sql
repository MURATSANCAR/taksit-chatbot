-- V010: Expand catalog_revisions status for two-stage publish.
-- Additive: widen CHECK; do not rename or drop data.

ALTER TABLE catalog_revisions DROP CONSTRAINT IF EXISTS catalog_revisions_status_check;

ALTER TABLE catalog_revisions
    ADD CONSTRAINT catalog_revisions_status_check
    CHECK (status IN (
        'DRAFT',
        'PREPARING',
        'READY_TO_PUBLISH',
        'PUBLISHED',
        'FAILED',
        'SUPERSEDED',
        'ARCHIVED'
    ));

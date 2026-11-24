ALTER TABLE geo_unit
    ADD COLUMN IF NOT EXISTS source_division_id VARCHAR(255) NULL AFTER source_feature_id;

CREATE INDEX IF NOT EXISTS ix_geo_source_division
    ON geo_unit (source_id, source_division_id);


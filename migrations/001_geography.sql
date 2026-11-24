CREATE TABLE IF NOT EXISTS geo_source (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    slug VARCHAR(100) NOT NULL,
    title VARCHAR(255) NOT NULL,
    release_version VARCHAR(100) NOT NULL,
    source_url TEXT NULL,
    license_name VARCHAR(255) NULL,
    license_url TEXT NULL,
    attribution TEXT NULL,
    metadata_json JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_geo_source_release (slug, release_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS geo_import_job (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source_id BIGINT UNSIGNED NULL,
    country_iso3 CHAR(3) NOT NULL,
    admin_level TINYINT UNSIGNED NOT NULL,
    status ENUM('running','complete','failed') NOT NULL,
    download_url TEXT NULL,
    checkpoint_feature BIGINT UNSIGNED NOT NULL DEFAULT 0,
    inserted_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
    updated_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
    error_message TEXT NULL,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP NULL,
    PRIMARY KEY (id),
    KEY ix_geo_import_scope (country_iso3, admin_level, status),
    CONSTRAINT fk_geo_import_source FOREIGN KEY (source_id) REFERENCES geo_source(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS geo_unit (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source_id BIGINT UNSIGNED NOT NULL,
    source_feature_id VARCHAR(255) NOT NULL,
    parent_id BIGINT UNSIGNED NULL,
    parent_relation_method ENUM('source','spatial_containment','none') NOT NULL DEFAULT 'none',
    country_iso3 CHAR(3) NOT NULL,
    admin_level TINYINT UNSIGNED NOT NULL,
    admin_type VARCHAR(100) NULL,
    name VARCHAR(500) NOT NULL,
    name_local VARCHAR(500) NULL,
    iso_code VARCHAR(100) NULL,
    centroid_lon DOUBLE NOT NULL,
    centroid_lat DOUBLE NOT NULL,
    min_lon DOUBLE NOT NULL,
    min_lat DOUBLE NOT NULL,
    max_lon DOUBLE NOT NULL,
    max_lat DOUBLE NOT NULL,
    area_sq_km DOUBLE NULL,
    geometry GEOMETRY NOT NULL,
    geometry_sha256 BINARY(32) NOT NULL,
    properties_json JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_geo_source_feature (source_id, source_feature_id),
    KEY ix_geo_country_level (country_iso3, admin_level),
    KEY ix_geo_parent (parent_id),
    KEY ix_geo_admin_type (admin_type),
    KEY ix_geo_bbox (min_lon, max_lon, min_lat, max_lat),
    SPATIAL KEY sx_geo_geometry (geometry),
    CONSTRAINT fk_geo_unit_source FOREIGN KEY (source_id) REFERENCES geo_source(id),
    CONSTRAINT fk_geo_unit_parent FOREIGN KEY (parent_id) REFERENCES geo_unit(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS geo_name (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    unit_id BIGINT UNSIGNED NOT NULL,
    language_code VARCHAR(35) NOT NULL DEFAULT 'und',
    script_code VARCHAR(20) NULL,
    name VARCHAR(500) NOT NULL,
    name_kind ENUM('primary','official','local','alias','short') NOT NULL DEFAULT 'alias',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_geo_name (unit_id, language_code, name_kind, name(191)),
    KEY ix_geo_name_language (language_code),
    CONSTRAINT fk_geo_name_unit FOREIGN KEY (unit_id) REFERENCES geo_unit(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


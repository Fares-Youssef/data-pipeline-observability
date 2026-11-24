CREATE TABLE IF NOT EXISTS weather_sources (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    slug VARCHAR(100) NOT NULL,
    name VARCHAR(255) NOT NULL,
    organization VARCHAR(255) NOT NULL,
    distributor VARCHAR(255) NOT NULL,
    base_url VARCHAR(500) NULL,
    license_name VARCHAR(255) NULL,
    terms_url VARCHAR(500) NULL,
    metadata_json JSON NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_weather_source_slug (slug)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS weather_models (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source_id BIGINT UNSIGNED NOT NULL,
    slug VARCHAR(100) NOT NULL,
    display_name VARCHAR(255) NOT NULL,
    organization VARCHAR(255) NOT NULL,
    provider_model_id VARCHAR(150) NOT NULL,
    grid_resolution_km DECIMAL(8,3) NULL,
    maximum_horizon_days SMALLINT UNSIGNED NULL,
    run_frequency_hours SMALLINT UNSIGNED NULL,
    metadata_json JSON NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_weather_model_slug (slug),
    UNIQUE KEY uq_weather_provider_model (source_id, provider_model_id),
    CONSTRAINT fk_weather_model_source FOREIGN KEY (source_id) REFERENCES weather_sources(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS weather_sampling_points (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    latitude DECIMAL(10,7) NOT NULL,
    longitude DECIMAL(10,7) NOT NULL,
    elevation_m DECIMAL(9,2) NULL,
    point_method ENUM('polygon_representative','station','manual','provider_grid') NOT NULL,
    timezone_name VARCHAR(64) NOT NULL DEFAULT 'Africa/Cairo',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    forecast_coverage BOOLEAN NOT NULL DEFAULT FALSE,
    observation_coverage BOOLEAN NOT NULL DEFAULT FALSE,
    metadata_json JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_weather_sampling_coordinate (latitude, longitude, point_method),
    KEY ix_weather_sampling_active (active, forecast_coverage)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS weather_geo_unit_points (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    geo_unit_id BIGINT UNSIGNED NOT NULL,
    sampling_point_id BIGINT UNSIGNED NOT NULL,
    role ENUM('primary','secondary','station_evaluation') NOT NULL DEFAULT 'primary',
    mapping_method VARCHAR(100) NOT NULL,
    representative BOOLEAN NOT NULL DEFAULT TRUE,
    distance_m DECIMAL(12,2) NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_weather_geo_point_role (geo_unit_id, sampling_point_id, role),
    KEY ix_weather_geo_primary (geo_unit_id, role, active),
    KEY ix_weather_point_units (sampling_point_id, active),
    CONSTRAINT fk_weather_geo_unit FOREIGN KEY (geo_unit_id) REFERENCES geo_unit(id),
    CONSTRAINT fk_weather_geo_point FOREIGN KEY (sampling_point_id) REFERENCES weather_sampling_points(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS weather_stations (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source_id BIGINT UNSIGNED NOT NULL,
    source_station_id VARCHAR(150) NOT NULL,
    name VARCHAR(500) NULL,
    country_code VARCHAR(3) NOT NULL,
    latitude DECIMAL(10,7) NOT NULL,
    longitude DECIMAL(10,7) NOT NULL,
    elevation_m DECIMAL(9,2) NULL,
    has_tmax BOOLEAN NOT NULL DEFAULT FALSE,
    has_tmin BOOLEAN NOT NULL DEFAULT FALSE,
    first_observation_date DATE NULL,
    last_observation_date DATE NULL,
    reporting_frequency VARCHAR(100) NULL,
    reporting_day_convention VARCHAR(255) NULL,
    quality_metadata_json JSON NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_weather_station_source (source_id, source_station_id),
    KEY ix_weather_station_country_recent (country_code, last_observation_date),
    CONSTRAINT fk_weather_station_source FOREIGN KEY (source_id) REFERENCES weather_sources(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS weather_station_point_matches (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    station_id BIGINT UNSIGNED NOT NULL,
    sampling_point_id BIGINT UNSIGNED NOT NULL,
    distance_km DECIMAL(10,3) NOT NULL,
    elevation_difference_m DECIMAL(10,2) NULL,
    temporal_alignment ENUM('verified','compatible','uncertain','incompatible') NOT NULL DEFAULT 'uncertain',
    suitability_score DECIMAL(7,4) NULL,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    metadata_json JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_weather_station_point (station_id, sampling_point_id),
    KEY ix_weather_point_station_primary (sampling_point_id, is_primary, suitability_score),
    CONSTRAINT fk_weather_match_station FOREIGN KEY (station_id) REFERENCES weather_stations(id),
    CONSTRAINT fk_weather_match_point FOREIGN KEY (sampling_point_id) REFERENCES weather_sampling_points(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS weather_pipeline_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    job_type VARCHAR(80) NOT NULL,
    logical_date DATE NULL,
    lock_key VARCHAR(191) NULL,
    status ENUM('running','succeeded','partial','failed','cancelled') NOT NULL,
    started_at_utc DATETIME(6) NOT NULL,
    finished_at_utc DATETIME(6) NULL,
    processed_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
    inserted_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
    duplicate_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
    failed_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
    error_message TEXT NULL,
    metadata_json JSON NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_weather_pipeline_lock (lock_key),
    KEY ix_weather_pipeline_type_date (job_type, logical_date, status),
    KEY ix_weather_pipeline_started (started_at_utc)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS weather_pipeline_batches (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    pipeline_run_id BIGINT UNSIGNED NOT NULL,
    source_id BIGINT UNSIGNED NULL,
    batch_key VARCHAR(191) NOT NULL,
    status ENUM('pending','running','succeeded','failed','skipped') NOT NULL,
    attempt_count SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    started_at_utc DATETIME(6) NULL,
    finished_at_utc DATETIME(6) NULL,
    processed_count INT UNSIGNED NOT NULL DEFAULT 0,
    inserted_count INT UNSIGNED NOT NULL DEFAULT 0,
    duplicate_count INT UNSIGNED NOT NULL DEFAULT 0,
    failed_count INT UNSIGNED NOT NULL DEFAULT 0,
    checkpoint_json JSON NULL,
    error_message TEXT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_weather_pipeline_batch (pipeline_run_id, batch_key),
    KEY ix_weather_batch_status (status, attempt_count),
    CONSTRAINT fk_weather_batch_run FOREIGN KEY (pipeline_run_id) REFERENCES weather_pipeline_runs(id),
    CONSTRAINT fk_weather_batch_source FOREIGN KEY (source_id) REFERENCES weather_sources(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS weather_forecast_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    model_id BIGINT UNSIGNED NOT NULL,
    pipeline_batch_id BIGINT UNSIGNED NULL,
    source_run_key VARCHAR(191) NOT NULL,
    run_kind ENUM('live_latest','specific_run','archived_run') NOT NULL,
    initialization_at_utc DATETIME(6) NULL,
    issued_at_utc DATETIME(6) NULL,
    collected_at_utc DATETIME(6) NOT NULL,
    api_version VARCHAR(100) NULL,
    model_version VARCHAR(100) NULL,
    forecast_timezone VARCHAR(64) NOT NULL DEFAULT 'Africa/Cairo',
    request_hash BINARY(32) NOT NULL,
    response_archive_uri VARCHAR(1000) NULL,
    response_sha256 BINARY(32) NULL,
    metadata_json JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_weather_forecast_source_run (model_id, source_run_key, request_hash),
    KEY ix_weather_forecast_model_init (model_id, initialization_at_utc),
    KEY ix_weather_forecast_collected (collected_at_utc),
    CONSTRAINT fk_weather_run_model FOREIGN KEY (model_id) REFERENCES weather_models(id),
    CONSTRAINT fk_weather_run_batch FOREIGN KEY (pipeline_batch_id) REFERENCES weather_pipeline_batches(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS weather_daily_forecasts (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    forecast_run_id BIGINT UNSIGNED NOT NULL,
    sampling_point_id BIGINT UNSIGNED NOT NULL,
    target_date DATE NOT NULL,
    lead_days TINYINT UNSIGNED NOT NULL,
    temperature_max_c DECIMAL(6,2) NULL,
    temperature_min_c DECIMAL(6,2) NULL,
    requested_latitude DECIMAL(10,7) NOT NULL,
    requested_longitude DECIMAL(10,7) NOT NULL,
    model_grid_latitude DECIMAL(10,7) NULL,
    model_grid_longitude DECIMAL(10,7) NULL,
    forecast_day_timezone VARCHAR(64) NOT NULL,
    forecast_day_start_utc DATETIME NULL,
    forecast_day_end_utc DATETIME NULL,
    quality_status ENUM('valid','partial','unavailable','rejected') NOT NULL DEFAULT 'valid',
    source_payload_index INT UNSIGNED NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_weather_daily_forecast (forecast_run_id, sampling_point_id, target_date),
    KEY ix_weather_forecast_point_target (sampling_point_id, target_date, lead_days),
    KEY ix_weather_forecast_target_lead (target_date, lead_days),
    CONSTRAINT chk_weather_lead_days CHECK (lead_days BETWEEN 0 AND 10),
    CONSTRAINT fk_weather_daily_run FOREIGN KEY (forecast_run_id) REFERENCES weather_forecast_runs(id),
    CONSTRAINT fk_weather_daily_point FOREIGN KEY (sampling_point_id) REFERENCES weather_sampling_points(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS weather_daily_observations (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    station_id BIGINT UNSIGNED NOT NULL,
    observation_date DATE NOT NULL,
    temperature_max_c DECIMAL(6,2) NULL,
    temperature_min_c DECIMAL(6,2) NULL,
    source_observation_id VARCHAR(255) NOT NULL,
    source_revision_key VARCHAR(191) NOT NULL,
    quality_flags VARCHAR(255) NULL,
    reporting_day_convention VARCHAR(255) NULL,
    observation_start_utc DATETIME NULL,
    observation_end_utc DATETIME NULL,
    temporal_alignment ENUM('verified','compatible','uncertain','incompatible') NOT NULL DEFAULT 'uncertain',
    collected_at_utc DATETIME(6) NOT NULL,
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_weather_observation_revision (station_id, observation_date, source_revision_key),
    KEY ix_weather_observation_current (station_id, observation_date, is_current),
    CONSTRAINT fk_weather_observation_station FOREIGN KEY (station_id) REFERENCES weather_stations(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS weather_observation_revisions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    observation_id BIGINT UNSIGNED NOT NULL,
    superseded_observation_id BIGINT UNSIGNED NULL,
    revision_reason VARCHAR(255) NULL,
    source_update_at_utc DATETIME(6) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_weather_observation_revision_link (observation_id),
    CONSTRAINT fk_weather_revision_observation FOREIGN KEY (observation_id) REFERENCES weather_daily_observations(id),
    CONSTRAINT fk_weather_revision_previous FOREIGN KEY (superseded_observation_id) REFERENCES weather_daily_observations(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS weather_quality_issues (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    pipeline_run_id BIGINT UNSIGNED NULL,
    entity_type VARCHAR(80) NOT NULL,
    entity_id BIGINT UNSIGNED NULL,
    issue_code VARCHAR(100) NOT NULL,
    severity ENUM('info','warning','error','critical') NOT NULL,
    message TEXT NOT NULL,
    details_json JSON NULL,
    resolved_at_utc DATETIME(6) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY ix_weather_quality_open (issue_code, severity, resolved_at_utc),
    CONSTRAINT fk_weather_quality_run FOREIGN KEY (pipeline_run_id) REFERENCES weather_pipeline_runs(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO weather_sources
    (slug, name, organization, distributor, base_url, license_name, terms_url, metadata_json)
VALUES
    ('open_meteo_gfs', 'NOAA GFS via Open-Meteo', 'NOAA/NCEP', 'Open-Meteo', 'https://api.open-meteo.com/v1/gfs', 'Open-Meteo terms and upstream NOAA terms', 'https://open-meteo.com/en/terms', JSON_OBJECT('forecast_origin','NOAA GFS')),
    ('open_meteo_ecmwf', 'ECMWF forecasts via Open-Meteo', 'ECMWF', 'Open-Meteo', 'https://api.open-meteo.com/v1/ecmwf', 'ECMWF open data CC-BY 4.0 / Open-Meteo terms', 'https://open-meteo.com/en/terms', JSON_OBJECT('forecast_origin','ECMWF')),
    ('noaa_ghcn_daily', 'GHCN-Daily', 'NOAA/NCEI', 'NOAA/NCEI', 'https://www.ncei.noaa.gov/products/land-based-station/global-historical-climatology-network-daily', 'Public data - review source terms', 'https://www.ncei.noaa.gov/products/land-based-station/global-historical-climatology-network-daily', JSON_OBJECT('observation_type','station'))
ON DUPLICATE KEY UPDATE name=VALUES(name), organization=VALUES(organization), distributor=VALUES(distributor), updated_at=CURRENT_TIMESTAMP;

INSERT INTO weather_models
    (source_id, slug, display_name, organization, provider_model_id, grid_resolution_km, maximum_horizon_days, run_frequency_hours, metadata_json)
SELECT id, 'gfs_global', 'NOAA GFS Global', 'NOAA/NCEP', 'gfs_global', 13.000, 16, 6, JSON_OBJECT('explicit_model', TRUE)
FROM weather_sources WHERE slug='open_meteo_gfs'
ON DUPLICATE KEY UPDATE display_name=VALUES(display_name), grid_resolution_km=VALUES(grid_resolution_km), maximum_horizon_days=VALUES(maximum_horizon_days), updated_at=CURRENT_TIMESTAMP;

INSERT INTO weather_models
    (source_id, slug, display_name, organization, provider_model_id, grid_resolution_km, maximum_horizon_days, run_frequency_hours, metadata_json)
SELECT id, 'ecmwf_ifs', 'ECMWF IFS HRES', 'ECMWF', 'ecmwf_ifs', 9.000, 15, 6, JSON_OBJECT('explicit_model', TRUE)
FROM weather_sources WHERE slug='open_meteo_ecmwf'
ON DUPLICATE KEY UPDATE display_name=VALUES(display_name), grid_resolution_km=VALUES(grid_resolution_km), maximum_horizon_days=VALUES(maximum_horizon_days), updated_at=CURRENT_TIMESTAMP;

INSERT INTO weather_models
    (source_id, slug, display_name, organization, provider_model_id, grid_resolution_km, maximum_horizon_days, run_frequency_hours, metadata_json)
SELECT id, 'ecmwf_aifs025_single', 'ECMWF AIFS 0.25° Single', 'ECMWF', 'ecmwf_aifs025_single', 28.000, 15, 6, JSON_OBJECT('explicit_model', TRUE)
FROM weather_sources WHERE slug='open_meteo_ecmwf'
ON DUPLICATE KEY UPDATE display_name=VALUES(display_name), grid_resolution_km=VALUES(grid_resolution_km), maximum_horizon_days=VALUES(maximum_horizon_days), updated_at=CURRENT_TIMESTAMP;
